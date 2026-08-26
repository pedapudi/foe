#!/usr/bin/perl
use strict;
use warnings;

my $templates_path = "/app/sequences.fasta";
my $primers_path = "/app/primers.fasta";

sub finding {
    print shift, "\n";
    exit 0;
}

sub reverse_complement {
    my $sequence = reverse shift;
    $sequence =~ tr/ACGTacgt/TGCAtgca/;
    return uc $sequence;
}

sub read_fasta {
    my ($path) = @_;
    open my $file, "<", $path or finding("$path does not exist");
    my (%records, @order);
    my $name;
    while (my $line = <$file>) {
        chomp $line;
        $line =~ s/\r$//;
        finding("$path contains a blank line") if $line eq "";
        if ($line =~ /^>([^\s].*)$/) {
            $name = $1;
            finding("$path repeats the header $name") if exists $records{$name};
            push @order, $name;
            $records{$name} = "";
            next;
        }
        finding("$path has sequence data before its first header") unless defined $name;
        finding("$path contains a non-DNA sequence for $name") unless $line =~ /^[ACGTacgt]+$/;
        $records{$name} .= uc $line;
    }
    close $file or die "closing $path: $!";
    finding("$path contains no records") unless @order;
    return (\%records, \@order);
}

sub circular_before {
    my ($sequence, $boundary, $length) = @_;
    my $doubled = $sequence . $sequence;
    return substr($doubled, length($sequence) + $boundary - $length, $length);
}

sub circular_after {
    my ($sequence, $boundary, $length) = @_;
    return substr($sequence . $sequence, $boundary, $length);
}

sub melting_temperature {
    my ($sequence) = @_;
    my @command = (
        "/usr/bin/oligotm", "-tp", "1", "-sc", "1", "-mv", "50",
        "-dv", "2", "-n", "0.8", "-d", "500", $sequence,
    );
    open my $file, "-|", @command or die "executing /usr/bin/oligotm: $!";
    local $/;
    my $value = <$file>;
    close $file or die "oligotm failed for $sequence\n";
    $value =~ s/^\s+|\s+$//g;
    die "oligotm returned an invalid value for $sequence\n"
        unless $value =~ /^-?[0-9]+(?:\.[0-9]+)?$/;
    return 0 + $value;
}

my ($templates, $template_order) = read_fasta($templates_path);
finding("$templates_path must contain input and output records")
    unless exists $templates->{input} && exists $templates->{output};
finding("$templates_path must contain exactly two records")
    unless @$template_order == 2;

my ($primers, $primer_order) = read_fasta($primers_path);
finding("$primers_path must contain exactly one forward and reverse primer pair")
    unless @$primer_order == 2;
finding("/usr/bin/oligotm is unavailable; install the primer3 package before validation")
    unless -x "/usr/bin/oligotm";

my $input = $templates->{input};
my $output = $templates->{output};
my $forward = $primers->{$primer_order->[0]};
my $reverse = $primers->{$primer_order->[1]};
my $insert_length = length($output) - length($input);
finding("the output must be longer than the input for this insertion task")
    unless $insert_length > 0;

my @output_decompositions;
for my $boundary (0 .. length($input)) {
    next unless substr($output, 0, $boundary) eq substr($input, 0, $boundary);
    next unless substr($output, $boundary + $insert_length) eq substr($input, $boundary);
    push @output_decompositions,
        [$boundary, substr($output, $boundary, $insert_length)];
}
finding("the supplied input and output do not describe one linear insertion")
    unless @output_decompositions;

my $primer_product = reverse_complement($reverse) . $forward;
my @failed_interpretations;
my $joint_interpretations = 0;
for my $decomposition (@output_decompositions) {
    my ($boundary, $insert) = @$decomposition;
    for my $insert_start (0 .. length($primer_product) - length($insert)) {
        next unless substr($primer_product, $insert_start, length($insert)) eq $insert;
        my $annealed_reverse_top = substr($primer_product, 0, $insert_start);
        my $annealed_forward = substr($primer_product, $insert_start + length($insert));
        my $reverse_length = length($annealed_reverse_top);
        my $forward_length = length($annealed_forward);
        next unless circular_before($input, $boundary, $reverse_length)
            eq $annealed_reverse_top;
        next unless circular_after($input, $boundary, $forward_length)
            eq $annealed_forward;
        $joint_interpretations++;

        my @problems;
        push @problems, "forward annealing length $forward_length is outside 15 through 45"
            if $forward_length < 15 || $forward_length > 45;
        push @problems, "reverse annealing length $reverse_length is outside 15 through 45"
            if $reverse_length < 15 || $reverse_length > 45;
        if (!@problems) {
            my $forward_tm = melting_temperature($annealed_forward);
            my $reverse_tm = melting_temperature(reverse_complement($annealed_reverse_top));
            my $difference = abs($forward_tm - $reverse_tm);
            push @problems, "forward melting temperature $forward_tm is outside 58 through 72"
                if $forward_tm < 58 || $forward_tm > 72;
            push @problems, "reverse melting temperature $reverse_tm is outside 58 through 72"
                if $reverse_tm < 58 || $reverse_tm > 72;
            push @problems,
                "primer temperatures differ by $difference degrees Celsius; expected at most 5"
                if $difference > 5;
        }
        exit 0 unless @problems;
        push @failed_interpretations, "boundary $boundary: " . join("; ", @problems);
    }
}

finding("no jointly consistent decomposition assigns the insert and both annealing regions to the final primer pair")
    unless $joint_interpretations;
finding($failed_interpretations[0]);

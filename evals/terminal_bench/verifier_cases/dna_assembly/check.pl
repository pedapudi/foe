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
    my ($path, $expected_lines) = @_;
    open my $file, "<", $path or finding("$path does not exist");
    my @lines = <$file>;
    close $file or die "closing $path: $!";
    chomp @lines;
    finding("$path contains a blank line") if grep { $_ eq "" } @lines;
    finding("$path has " . scalar(@lines) . " lines; expected $expected_lines")
        if defined($expected_lines) && @lines != $expected_lines;
    my %records;
    my $name;
    for my $line (@lines) {
        if ($line =~ /^>([^\s]+)$/) {
            $name = $1;
            finding("$path repeats the header $name") if exists $records{$name};
            $records{$name} = "";
            next;
        }
        finding("$path has sequence data before its first header") unless defined $name;
        finding("$path contains a non-DNA sequence for $name") unless $line =~ /^[ACGTacgt]+$/;
        $records{$name} .= uc $line;
    }
    return %records;
}

sub parse_primer {
    my ($name, $sequence) = @_;
    my $site = index($sequence, "GGTCTC");
    finding("primer $name has no BsaI recognition site") if $site < 0;
    finding("primer $name has fewer than six bases before its BsaI site") if $site < 6;
    finding("primer $name has more than one BsaI recognition site")
        if index($sequence, "GGTCTC", $site + 1) >= 0;
    my $tail = substr($sequence, $site + 6);
    finding("primer $name lacks a spacer, four-base overhang, or binding tract")
        if length($tail) < 20;
    return (substr($tail, 1, 4), substr($tail, 5));
}

sub forward_overlap {
    my ($overhang, $template_before) = @_;
    for (my $length = 4; $length > 0; $length--) {
        return $length
            if substr($overhang, -$length) eq substr($template_before, -$length);
    }
    return 0;
}

sub reverse_overlap {
    my ($overhang, $template_after) = @_;
    for (my $length = 4; $length > 0; $length--) {
        return $length
            if reverse_complement(substr($overhang, -$length))
                eq substr($template_after, 0, $length);
    }
    return 0;
}

sub melting_temperature {
    my ($name, $sequence) = @_;
    my $command = "/usr/bin/oligotm -tp 1 -sc 1 -mv 50 -dv 2 -n 0.8 -d 500 $sequence";
    my $value = `$command`;
    die "oligotm failed for $name\n" if $? != 0;
    chomp $value;
    die "oligotm returned an invalid value for $name\n" unless $value =~ /^-?[0-9]+(?:\.[0-9]+)?$/;
    return 0 + $value;
}

my %templates = read_fasta($templates_path, undef);
my %primers = read_fasta($primers_path, 16);
for my $template (qw(input egfp flag snap output)) {
    finding("$templates_path lacks $template") unless exists $templates{$template};
}
for my $name (qw(input_fwd input_rev egfp_fwd egfp_rev flag_fwd flag_rev snap_fwd snap_rev)) {
    finding("$primers_path lacks $name") unless exists $primers{$name};
}
finding("$primers_path must contain exactly eight records") if keys(%primers) != 8;
finding("/usr/bin/oligotm is unavailable; install the primer3 package before validation")
    unless -x "/usr/bin/oligotm";

my (%forward_overhang, %reverse_overhang);
for my $template (qw(input egfp flag snap)) {
    my ($forward_oh, $forward_bind) = parse_primer("${template}_fwd", $primers{"${template}_fwd"});
    my ($reverse_oh, $reverse_bind) = parse_primer("${template}_rev", $primers{"${template}_rev"});
    my $observed = $templates{$template};
    $observed .= $observed if $template eq "input";
    my $forward_start = index($observed, $forward_bind);
    finding("forward primer for $template does not bind its template") if $forward_start < 0;
    my $reverse_top = reverse_complement($reverse_bind);
    my $reverse_start = index($observed, $reverse_top, $forward_start + length($forward_bind));
    finding("reverse primer for $template does not bind downstream of its forward primer")
        if $reverse_start < 0;

    my $before = substr($observed, 0, $forward_start);
    my $forward_overlap = forward_overlap($forward_oh, substr($before, -4));
    my $after_start = $reverse_start + length($reverse_top);
    my $after = substr($observed, $after_start, 4);
    my $reverse_overlap = reverse_overlap($reverse_oh, $after);
    my $forward_annealed = substr($observed, $forward_start - $forward_overlap,
        $forward_overlap + length($forward_bind));
    my $reverse_annealed_top = substr($observed, $reverse_start,
        length($reverse_top) + $reverse_overlap);
    my $reverse_annealed = reverse_complement($reverse_annealed_top);

    my $forward_length = length($forward_annealed);
    my $reverse_length = length($reverse_annealed);
    finding("forward primer for $template anneals across $forward_length bases; expected 15 through 45")
        if $forward_length < 15 || $forward_length > 45;
    finding("reverse primer for $template anneals across $reverse_length bases; expected 15 through 45")
        if $reverse_length < 15 || $reverse_length > 45;

    my $forward_tm = melting_temperature("${template}_fwd", $forward_annealed);
    my $reverse_tm = melting_temperature("${template}_rev", $reverse_annealed);
    finding("forward primer for $template has melting temperature $forward_tm; expected 58 through 72")
        if $forward_tm < 58 || $forward_tm > 72;
    finding("reverse primer for $template has melting temperature $reverse_tm; expected 58 through 72")
        if $reverse_tm < 58 || $reverse_tm > 72;
    my $difference = abs($forward_tm - $reverse_tm);
    finding("primer pair for $template differs by $difference degrees Celsius; expected at most 5")
        if $difference > 5;
    $forward_overhang{$template} = $forward_oh;
    $reverse_overhang{$template} = $reverse_oh;
}

my @order = qw(input egfp flag snap);
for my $index (0 .. $#order) {
    my $current = $order[$index];
    my $next = $order[($index + 1) % @order];
    finding("junction from $current to $next has incompatible overhangs")
        unless reverse_complement($reverse_overhang{$current}) eq $forward_overhang{$next};
}

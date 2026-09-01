#!/usr/bin/perl
use strict;
use warnings;
use Digest::SHA qw(sha256_hex);

my $main_path = "/app/main.tex";
my $input_path = "/app/input.tex";
my $synonyms_path = "/app/synonyms.txt";
my $main_sha256 = "6c07d3203fdc3fc54317fafeee9d19b576418c9de2e142a599814b2f6cb32b2f";
my $synonyms_sha256 = "bc8f7cf4b41914be036e6d4666e1550971d39b2f23b7e69e3e975f4bdb387be6";
my $normalized_input_sha256 =
    "507095702f21ab9b41b404a01679c33fd53683eeb30910ac5d90f3ce9b9a449c";

sub finding {
    print shift, "\n";
    exit 0;
}

sub read_bytes {
    my ($path) = @_;
    open my $file, "<", $path or finding("$path does not exist");
    binmode $file;
    local $/;
    my $value = <$file>;
    close $file or die "closing $path: $!";
    return $value;
}

sub normalized_input_digest {
    my ($text, $synonyms_text) = @_;
    my %canonical;
    for my $line (split /\n/, $synonyms_text) {
        next if $line eq "";
        my @words = split /, /, $line;
        finding("$synonyms_path contains an empty family") unless @words;
        for my $word (@words) {
            finding("$synonyms_path repeats the word $word") if exists $canonical{$word};
            $canonical{$word} = $words[0];
        }
    }

    $text =~ s/---/ --- /g;
    my $normalized = "";
    for my $token ($text =~ /(\S+)/g) {
        finding("$input_path contains a token that cannot be classified")
            unless $token =~ /^(\W*)(.*?)(\W*)$/;
        my ($prefix, $word, $suffix) = ($1, $2, $3);
        my $replacement = exists $canonical{$word} ? $canonical{$word} : $word;
        for my $part ($prefix, $replacement, $suffix) {
            $normalized .= pack("N", length($part)) . $part;
        }
    }
    return sha256_hex($normalized);
}

my $main = read_bytes($main_path);
my $input = read_bytes($input_path);
my $synonyms = read_bytes($synonyms_path);
finding("$main_path differs from the task-supplied file")
    unless sha256_hex($main) eq $main_sha256;
finding("$synonyms_path differs from the task-supplied file")
    unless sha256_hex($synonyms) eq $synonyms_sha256;
finding("$input_path contains a change outside the declared synonym families")
    unless normalized_input_digest($input, $synonyms) eq $normalized_input_sha256;

chdir "/app" or die "changing to /app: $!";
my $compile = qx{/usr/bin/pdflatex -interaction=nonstopmode -halt-on-error main.tex 2>&1};
finding("pdflatex failed: $compile") unless $? == 0;
my $log = read_bytes("/app/main.log");
finding("pdflatex did not record a completed PDF")
    unless $log =~ /Output written on main\.pdf/;
finding("main.tex still produces an overfull hbox")
    if $log =~ /Overfull \\hbox/;

exit 0;

#!/bin/sh
set -eu

/usr/bin/apt-get update -qq
/usr/bin/apt-get install -y -qq primer3
/bin/cat > /app/primers.fasta <<'FASTA'
>insertion_forward
AGTAGATTAGAAGAAGAATTAAGAAGAAGATTAACAGAAAGCAAGGGCGAGGAG
>insertion_reverse
CATATGTATATCTCCTTCTTAAAGTTAAAC
FASTA

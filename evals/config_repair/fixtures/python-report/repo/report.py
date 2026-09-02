"""Write report.txt from measurements.csv: one line per sample, then a total."""

import csv

with open("measurements.csv", newline="", encoding="utf-8") as source:
    rows = list(csv.DictReader(source))
lines = [f"{row['sample']} {int(row['value'])}" for row in rows]
lines.append(f"total {sum(int(row['value']) for row in rows)}")
with open("report.txt", "w", encoding="utf-8") as report:
    report.write("\n".join(lines) + "\n")

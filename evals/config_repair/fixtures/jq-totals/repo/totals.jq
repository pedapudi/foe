# Write one "<category> <total quantity>" line per category, sorted by
# category, from the inventory document on the command line.
.items
| group_by(.category)
| map("\(.[0].category) \(map(.quantity) | add)")
| .[]

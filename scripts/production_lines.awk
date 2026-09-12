# Counts the production lines of the Rust files it is given: every line that
# is neither blank nor a comment and lies outside an inline test module.
#
# An inline test module runs from `mod tests {` under a `#[cfg(test)]`
# attribute to the `}` that closes it, which rustfmt places alone in the
# first column while indenting every line inside. Ending the skip at that
# brace rather than at the end of the file keeps production code written
# after a test module counted; otherwise a file whose test module comes
# first declares no lines at all and passes any ceiling.
FNR == 1 { test_only = 0; test_attribute = 0 }
test_only && /^\}$/ { test_only = 0; next }
test_only { next }
/^#\[cfg\(test\)\]$/ { test_attribute = 1; next }
test_attribute && /^mod tests \{$/ { test_only = 1; next }
{
  if ($0 !~ /^[[:space:]]*$/ && $0 !~ /^[[:space:]]*\/\//) lines++
  test_attribute = 0
}
END { print lines + 0 }

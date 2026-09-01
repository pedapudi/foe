//! Interpreters that the kernel opens while starting an executable image.
//!
//! A Landlock execute rule for a script or dynamically linked ELF image is
//! insufficient by itself. The kernel also executes the script interpreter
//! or the ELF `PT_INTERP` file. This module reads those exact paths from the
//! executable bytes so the sandbox can authorize individual files.

use std::path::PathBuf;

const ELF_MAGIC: &[u8] = b"\x7fELF";
const PT_INTERP: u32 = 3;

/// Returns the absolute interpreter path the kernel will execute, if any.
/// Statically linked ELF images return `None`.
pub fn interpreter(image: &[u8]) -> Result<Option<PathBuf>, String> {
    if image.starts_with(b"#!") {
        return shebang_interpreter(image).map(Some);
    }
    if image.starts_with(ELF_MAGIC) {
        return elf_interpreter(image);
    }
    Err("has an unsupported executable format; expected ELF or an absolute shebang interpreter".into())
}

fn shebang_interpreter(image: &[u8]) -> Result<PathBuf, String> {
    let line_end = image.iter().position(|byte| *byte == b'\n').unwrap_or(image.len());
    let line = std::str::from_utf8(&image[2..line_end]).map_err(|_| "has a non-UTF-8 shebang".to_string())?;
    let selected = line.trim().split_ascii_whitespace().next().ok_or("has an empty shebang")?;
    let path = PathBuf::from(selected);
    if !path.is_absolute() {
        return Err(format!("has a relative shebang interpreter {selected:?}; use an absolute path"));
    }
    if selected == "/usr/bin/env" || selected == "/bin/env" {
        return Err("selects its interpreter through env; name the absolute interpreter in the shebang".into());
    }
    Ok(path)
}

fn elf_interpreter(image: &[u8]) -> Result<Option<PathBuf>, String> {
    let class = *image.get(4).ok_or("has a truncated ELF identification")?;
    let endian = *image.get(5).ok_or("has a truncated ELF identification")?;
    let (phoff, entry_size, count, offset_at, size_at) = match class {
        1 => (number(image, 28, 4, endian)?, number(image, 42, 2, endian)?, number(image, 44, 2, endian)?, 4, 16),
        2 => (number(image, 32, 8, endian)?, number(image, 54, 2, endian)?, number(image, 56, 2, endian)?, 8, 32),
        value => return Err(format!("uses unsupported ELF class {value}")),
    };
    if entry_size < size_at + if class == 1 { 4 } else { 8 } {
        return Err("has a truncated ELF program-header entry".into());
    }
    for index in 0..count {
        let start = phoff
            .checked_add(index.checked_mul(entry_size).ok_or("has overflowing ELF program headers")?)
            .ok_or("has overflowing ELF program headers")?;
        let kind = number(image, start, 4, endian)?;
        if kind != PT_INTERP as usize {
            continue;
        }
        let offset = number(image, start + offset_at, if class == 1 { 4 } else { 8 }, endian)?;
        let size = number(image, start + size_at, if class == 1 { 4 } else { 8 }, endian)?;
        let end = offset.checked_add(size).ok_or("has an overflowing ELF interpreter")?;
        let bytes = image.get(offset..end).ok_or("has a truncated ELF interpreter")?;
        let bytes = bytes.strip_suffix(&[0]).ok_or("has an unterminated ELF interpreter")?;
        let selected = std::str::from_utf8(bytes).map_err(|_| "has a non-UTF-8 ELF interpreter".to_string())?;
        let path = PathBuf::from(selected);
        if !path.is_absolute() {
            return Err(format!("has a relative ELF interpreter {selected:?}"));
        }
        return Ok(Some(path));
    }
    Ok(None)
}

fn number(image: &[u8], offset: usize, width: usize, endian: u8) -> Result<usize, String> {
    let end = offset.checked_add(width).ok_or("has an overflowing ELF header")?;
    let bytes = image.get(offset..end).ok_or("has a truncated ELF header")?;
    let value = match (endian, width) {
        (1, 2) => u16::from_le_bytes(bytes.try_into().unwrap()) as u64,
        (1, 4) => u32::from_le_bytes(bytes.try_into().unwrap()) as u64,
        (1, 8) => u64::from_le_bytes(bytes.try_into().unwrap()),
        (2, 2) => u16::from_be_bytes(bytes.try_into().unwrap()) as u64,
        (2, 4) => u32::from_be_bytes(bytes.try_into().unwrap()) as u64,
        (2, 8) => u64::from_be_bytes(bytes.try_into().unwrap()),
        (value, _) => return Err(format!("uses unsupported ELF byte order {value}")),
    };
    usize::try_from(value).map_err(|_| "has an ELF offset too large for this host".into())
}

#[cfg(test)]
#[path = "executable_support_test.rs"]
mod tests;

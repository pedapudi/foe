use super::*;

#[test]
fn direct_shebang_names_one_exact_interpreter() {
    assert_eq!(interpreter(b"#!/bin/sh -eu\necho ok\n").unwrap(), Some(PathBuf::from("/bin/sh")));
}

#[test]
fn path_searching_shebang_is_rejected() {
    assert_eq!(
        interpreter(b"#!/usr/bin/env python3\n").unwrap_err(),
        "selects its interpreter through env; name the absolute interpreter in the shebang"
    );
}

#[test]
fn host_elf_names_its_dynamic_loader() {
    let image = std::fs::read("/bin/true").unwrap();
    let selected = interpreter(&image).unwrap().expect("the host test binary is dynamically linked");
    assert!(selected.is_absolute());
    assert!(selected.is_file(), "{}", selected.display());
}

#[test]
fn static_elf_has_no_interpreter() {
    let mut image = vec![0_u8; 64];
    image[..6].copy_from_slice(b"\x7fELF\x02\x01");
    image[54..56].copy_from_slice(&56_u16.to_le_bytes());
    assert_eq!(interpreter(&image).unwrap(), None);
}

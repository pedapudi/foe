use super::*;
use foe_log::SandboxMode;
use std::path::PathBuf;

/// docs/sandbox.md "The episode process": the policy the kernel receives is
/// the one assembled while the process was still unconfined, and it is
/// received once. Mode `off` enforces nothing, so the test process itself
/// stays unrestricted; a mode that enforced could not be undone for the
/// rest of the test binary.
#[test]
fn confinement_carries_the_policy_assembled_before_it() {
    let sandbox = Arc::new(Sandbox::new(SandboxMode::Off).unwrap());
    let mut unconfined = Unconfined::new(sandbox, Policy::default());
    unconfined.policy_mut().bind_tcp.push(4321);
    unconfined.policy_mut().read_files.push(PathBuf::from("/etc/hostname"));
    assert_eq!(unconfined.parts().1.bind_tcp, vec![4321]);
    let confined = unconfined.enter().unwrap();
    let (sandbox, policy) = confined.parts();
    assert_eq!(sandbox.abi(), 0);
    assert_eq!(policy.bind_tcp, vec![4321]);
    assert_eq!(policy.read_files, vec![PathBuf::from("/etc/hostname")]);
}

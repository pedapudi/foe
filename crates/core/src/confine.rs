//! Entering confinement: the one point at which a process applies its own
//! Landlock ruleset to itself.
//!
//! Part of a policy is known only after work the policy itself would
//! forbid: the port a viewer listens on, the credential file a resolved
//! model transport reads. [`Unconfined`] owns the policy across that work
//! and is the only source of a mutable reference to it.
//! [`Unconfined::enter`] consumes that value, applies the ruleset to the
//! calling thread, and returns a [`Confined`], which lends the policy for
//! reading alone.
//!
//! Two orderings are therefore checked by the compiler rather than stated
//! in a comment. A change to the policy after enforcement does not compile,
//! because no mutable reference to it survives `enter`. A second
//! enforcement of the same policy does not compile, because `enter`
//! consumes the value that holds it. Code that has to run before
//! confinement takes an `&mut Unconfined`, which exists only before
//! `enter`.
//!
//! Neither guarantee reaches what the process does. A file read, a socket
//! bind, or a process start written after `enter` still compiles and still
//! runs; the kernel refuses it when the policy does not allow it, which is
//! the reason for enforcing at all. What the types settle is that the
//! policy the kernel receives is final and that the kernel receives it once.
//!
//! Landlock restricts the calling thread and every thread and process that
//! thread creates afterwards, so `enter` belongs on the main thread before
//! any other thread of the process exists. No type expresses that;
//! `docs/sandbox.md` "The episode process" states it.

use crate::sandbox::{Policy, Sandbox};
use crate::RuntimeError;
use std::sync::Arc;

/// A process that has not yet restricted itself, holding the policy it will
/// enforce and permitting additions to it.
pub struct Unconfined {
    sandbox: Arc<Sandbox>,
    policy: Policy,
}

impl Unconfined {
    pub fn new(sandbox: Arc<Sandbox>, policy: Policy) -> Unconfined {
        Unconfined { sandbox, policy }
    }

    /// Adds what the privileged work discovers: a port to bind, a
    /// credential file to read, an executable to run.
    pub fn policy_mut(&mut self) -> &mut Policy {
        &mut self.policy
    }

    /// The sandbox and the policy as they stand, for work that narrows a
    /// child process before this process confines itself.
    pub fn parts(&self) -> (&Arc<Sandbox>, &Policy) {
        (&self.sandbox, &self.policy)
    }

    /// Applies the policy to the calling thread and to everything that
    /// thread creates afterwards.
    pub fn enter(self) -> Result<Confined, RuntimeError> {
        self.sandbox.enforce_self(&self.policy)?;
        Ok(Confined { sandbox: self.sandbox, policy: self.policy })
    }
}

/// A process that has restricted itself. The policy it enforced is readable
/// and final.
pub struct Confined {
    sandbox: Arc<Sandbox>,
    policy: Policy,
}

impl Confined {
    /// The sandbox and the policy that was enforced. An executable this
    /// process starts is narrowed from both.
    pub fn parts(&self) -> (&Arc<Sandbox>, &Policy) {
        (&self.sandbox, &self.policy)
    }
}

#[cfg(test)]
#[path = "confine_test.rs"]
mod tests;

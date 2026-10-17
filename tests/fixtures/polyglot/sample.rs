use std::collections::HashMap;
use crate::error::{Error, Result};

/// A stored user.
#[derive(Debug, Clone)]
pub struct User {
    pub id: String,
}

/// Loads users.
pub trait Repo {
    fn find(&self, id: &str) -> Option<User>;
}

pub enum Role {
    Admin,
    Member,
}

pub struct MemoryRepo {
    users: HashMap<String, User>,
}

impl MemoryRepo {
    /// Build an empty repo.
    pub fn new() -> Self {
        MemoryRepo { users: HashMap::new() }
    }
}

impl Repo for MemoryRepo {
    fn find(&self, id: &str) -> Option<User> {
        self.users.get(id).cloned()
    }
}

/// Look a user up or fail.
pub fn lookup(id: &str) -> Result<User> {
    let repo = MemoryRepo::new();
    repo.find(id).ok_or(Error::NotFound)
}

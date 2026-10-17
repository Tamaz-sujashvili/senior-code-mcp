package store

import (
	"errors"
	"fmt"
)

// User is a stored user.
type User struct {
	ID   string
	Name string
}

// Repo loads users.
type Repo interface {
	Find(id string) (*User, error)
}

// MemoryRepo keeps users in memory.
type MemoryRepo struct {
	users map[string]*User
}

// Find returns the user with id.
func (r *MemoryRepo) Find(id string) (*User, error) {
	u, ok := r.users[id]
	if !ok {
		return nil, errors.New(fmt.Sprintf("no user %s", id))
	}
	return u, nil
}

// NewMemoryRepo builds an empty repo.
func NewMemoryRepo() *MemoryRepo {
	return &MemoryRepo{users: map[string]*User{}}
}

func Lookup(id string) string {
	r := NewMemoryRepo()
	u, _ := r.Find(id)
	return u.Name
}

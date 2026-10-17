import React from "react";

/** Renders a user's name. */
export function UserCard({ name }: { name: string }) {
  return <div className="card">{formatTitle(name)}</div>;
}

function formatTitle(s: string): string {
  return s.toUpperCase();
}

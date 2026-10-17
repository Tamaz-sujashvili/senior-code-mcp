import { readFile } from "fs/promises";
const path = require("path");

/** Load and parse a JSON config file. */
export async function loadConfig(file) {
  const raw = await readFile(path.resolve(file), "utf8");
  return parseConfig(raw);
}

function parseConfig(raw) {
  return JSON.parse(raw);
}

// Formats a user greeting.
const greet = (name) => formatName(name).trim();

function formatName(name) {
  return `Hello ${name}`;
}

export class UserStore extends BaseStore {
  /** Find a user by id. */
  find(id) {
    return this.db.get(id) ?? loadConfig("users.json");
  }
}

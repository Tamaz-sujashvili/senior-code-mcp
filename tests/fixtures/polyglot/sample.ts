import { Request, Response } from "express";
import * as db from "./db";

/** A user record. */
export interface User {
  id: string;
  name: string;
}

export type UserId = string;

export enum Role {
  Admin,
  Member,
}

export class UserService {
  constructor(private repo: db.Repo) {}

  /** Fetch one user, or throw. */
  async getUser(id: UserId): Promise<User> {
    const user = await this.repo.find(id);
    if (!user) throw new NotFoundError(id);
    return validateUser(user);
  }
}

export function validateUser(u: User): User {
  return u;
}

export const handler = async (req: Request, res: Response) => {
  res.json(await new UserService(db.repo).getUser(req.params.id));
};

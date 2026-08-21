import { readFileSync } from "node:fs";
import type { LogEvent } from "../src/types.js";

/** Reads one fixture log from view/fixtures/ as parsed events. */
export function fixture(name: string): LogEvent[] {
  const text = readFileSync(new URL(`../fixtures/${name}`, import.meta.url), "utf8");
  return text
    .split("\n")
    .filter((line) => line.trim() !== "")
    .map((line) => JSON.parse(line) as LogEvent);
}

// A task as its sections. A task a person wrote is one section of prose. A
// task a workflow node was given names each section with a `## heading`,
// and a section carries what an earlier node returned as the JSON that
// value travels as. The heading is that node's name, which a reader matches
// against the graph.
//
// The section holding the task itself is headed `task`, and the row that
// carries the task already says so, so that heading resolves to no title.

/** One section of a task: what it is called, and what it holds. */
export interface TaskSection {
  /** The workflow node the section came from, or null for the task itself. */
  readonly title: string | null;
  /** The section's text, always present. */
  readonly text: string;
  /** The value the text parses as, or null when it is prose. */
  readonly value: unknown | null;
}

/** The sections of a task, in the order the text gives them. */
export function taskSections(text: string): TaskSection[] {
  const out: TaskSection[] = [];
  const close = (name: string | null, lines: string[]): void => {
    const body = lines.join("\n").trim();
    if (body === "") return;
    out.push({ title: name === "task" ? null : name, text: body, value: parsed(body) });
  };
  let name: string | null = null;
  let lines: string[] = [];
  for (const line of text.split("\n")) {
    const heading = line.startsWith("## ") ? line.slice(3).trim() : null;
    if (heading === null) {
      lines.push(line);
      continue;
    }
    close(name, lines);
    name = heading;
    lines = [];
  }
  close(name, lines);
  return out;
}

/** The object or array the text is, or null when it is anything else. */
function parsed(text: string): unknown | null {
  if (!text.startsWith("{") && !text.startsWith("[")) return null;
  try {
    const value: unknown = JSON.parse(text);
    return value !== null && typeof value === "object" ? value : null;
  } catch {
    return null;
  }
}

import type { ReactNode } from "react";

export function SelectionRequired({
  kind,
  children,
}: {
  kind: "season" | "event";
  children: ReactNode;
}) {
  const article = kind === "event" ? "an" : "a";
  return (
    <div className="selection-required" role="status">
      <p className="eyebrow">Selection required</p>
      <h1>
        Choose {article} {kind}
      </h1>
      <p>
        Use the selector in the header to choose the {kind} whose stored data
        you want to inspect.
      </p>
      {children}
    </div>
  );
}

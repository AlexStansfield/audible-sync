/**
 * A screen not built yet: Milestone 4 adds them one at a time (todo.md), each in
 * place of this.
 */
export function ComingSoon({ title }: { title: string }) {
  return (
    <>
      <header className="page-header">
        <h1 className="page-title">{title}</h1>
      </header>
      <p className="muted" style={{ margin: 0 }}>
        This screen is still being built.
      </p>
    </>
  );
}

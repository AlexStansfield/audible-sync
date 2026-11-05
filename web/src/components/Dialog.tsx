/**
 * A modal dialog on the native <dialog>: `showModal()` makes the page behind inert,
 * which traps focus, and Escape closes it. A click on the backdrop closes it too.
 */
import { type ReactNode, useEffect, useId, useRef } from "react";
import styles from "./Dialog.module.css";
import { Icon } from "./Icon";

export function Dialog({
  open,
  onClose,
  title,
  children,
  actions,
  width = 480,
  closeButton = false,
}: {
  open: boolean;
  onClose: () => void;
  title: ReactNode;
  children?: ReactNode;
  /** The buttons along the foot, right-aligned */
  actions?: ReactNode;
  width?: number;
  /** A Close (×) button beside the title, for a dialog with no Cancel of its own */
  closeButton?: boolean;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) dialog.showModal();
    if (!open && dialog.open) dialog.close();
  }, [open]);

  return (
    // biome-ignore lint/a11y/useKeyWithClickEvents: the backdrop click; Escape (onCancel) is the keyboard equivalent
    <dialog
      ref={ref}
      aria-labelledby={titleId}
      className={styles.dialog}
      style={{ width: `min(${width}px, calc(100vw - 32px))` }}
      onCancel={(event) => {
        // Escape: the parent decides, so its state never disagrees with the element
        event.preventDefault();
        onClose();
      }}
      // A click on the backdrop lands on the <dialog> itself
      onClick={(event) => {
        if (event.target === ref.current) onClose();
      }}
    >
      {open && (
        <div className={styles.body}>
          <div className={styles.head}>
            <h2 id={titleId} className={styles.title}>
              {title}
            </h2>
            {closeButton && (
              <button type="button" className={`icon-btn ${styles.close}`} aria-label="Close" onClick={onClose}>
                <Icon name="close" size={18} strokeWidth={2.2} />
              </button>
            )}
          </div>
          {children}
          {actions && <div className={styles.actions}>{actions}</div>}
        </div>
      )}
    </dialog>
  );
}

/**
 * A confirmation for a destructive action - the only kind that confirms. The confirm
 * button is in the destructive style and names what it does.
 */
export function ConfirmDialog({
  open,
  title,
  children,
  confirmLabel,
  cancelLabel = "Cancel",
  busy = false,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: ReactNode;
  children?: ReactNode;
  confirmLabel: string;
  cancelLabel?: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <Dialog
      open={open}
      onClose={busy ? () => undefined : onCancel}
      title={title}
      actions={
        <>
          <button type="button" className="btn btn-secondary" onClick={onCancel} disabled={busy}>
            {cancelLabel}
          </button>
          <button type="button" className="btn btn-danger" onClick={onConfirm} disabled={busy}>
            {confirmLabel}
          </button>
        </>
      }
    >
      {children}
    </Dialog>
  );
}

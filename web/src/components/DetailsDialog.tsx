import { useEffect, useId, useRef } from "react";
import type { ReactNode } from "react";

export function DetailsDialog({ title, children, onClose, closeDisabled = false }: {
  title: string; children: ReactNode; onClose: () => void; closeDisabled?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  useEffect(() => {
    const element = dialog.current;
    element?.showModal();
    return () => element?.close();
  }, []);
  return <dialog ref={dialog} className="setup-dialog" aria-labelledby={titleId}
    onCancel={(event) => { event.preventDefault(); if (!closeDisabled) onClose(); }}>
    <header><h2 id={titleId}>{title}</h2><button type="button" disabled={closeDisabled} onClick={onClose}>Close</button></header>
    {children}
  </dialog>;
}

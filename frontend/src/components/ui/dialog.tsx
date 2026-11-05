// shadcn/ui Dialog composition over Radix, adapted as an accessible side sheet.
import * as DialogPrimitive from '@radix-ui/react-dialog';
import { X } from 'lucide-react';
import type { ReactNode } from 'react';

export const Dialog = DialogPrimitive.Root;
export const DialogTitle = DialogPrimitive.Title;
export const DialogDescription = DialogPrimitive.Description;
export function DialogContent({ children, onCloseAutoFocus }: { children: ReactNode; onCloseAutoFocus?: (event: Event) => void }) {
  return <DialogPrimitive.Portal>
    <DialogPrimitive.Overlay className="sheet-overlay" />
    <DialogPrimitive.Content className="sheet-content" onCloseAutoFocus={onCloseAutoFocus}>
      <DialogPrimitive.Close className="sheet-close" aria-label="Close review"><X size={20} /></DialogPrimitive.Close>
      {children}
    </DialogPrimitive.Content>
  </DialogPrimitive.Portal>;
}

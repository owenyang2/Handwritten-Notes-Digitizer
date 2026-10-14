// Owned shadcn/ui primitives, adapted to Quiet Notebook tokens.
import * as React from "react";
import * as DialogPrimitive from "@radix-ui/react-dialog";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";
import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";
import { X } from "lucide-react";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
const buttonVariants = cva(
  "ui-button inline-flex items-center justify-center gap-2 rounded-lg text-sm font-medium disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "ui-button-primary",
        secondary: "ui-button-secondary",
        ghost: "ui-button-ghost",
        destructive: "ui-button-danger",
      },
    },
    defaultVariants: { variant: "default" },
  },
);
export function Button({
  className,
  variant,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> &
  VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Component = asChild ? Slot : "button";
  return (
    <Component
      className={cn(buttonVariants({ variant }), className)}
      {...props}
    />
  );
}
export const Dialog = DialogPrimitive.Root;
export const DialogTrigger = DialogPrimitive.Trigger;
export function DialogContent({
  title,
  description,
  children,
  ...props
}: React.ComponentProps<typeof DialogPrimitive.Content> & {
  title: string;
  description: string;
}) {
  return (
    <DialogPrimitive.Portal>
      <DialogPrimitive.Overlay className="dialog-overlay" />
      <DialogPrimitive.Content className="dialog-content" {...props}>
        <DialogPrimitive.Title className="dialog-title">
          {title}
        </DialogPrimitive.Title>
        <DialogPrimitive.Description className="dialog-description">
          {description}
        </DialogPrimitive.Description>
        {children}
        <DialogPrimitive.Close asChild>
          <Button variant="ghost" className="dialog-close" aria-label="Close">
            <X size={18} />
          </Button>
        </DialogPrimitive.Close>
      </DialogPrimitive.Content>
    </DialogPrimitive.Portal>
  );
}

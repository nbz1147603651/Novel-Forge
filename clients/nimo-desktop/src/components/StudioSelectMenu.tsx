import { useEffect, useId, useRef, useState } from "react";

export interface StudioSelectOption<TValue extends string> {
  readonly value: TValue;
  readonly label: string;
  readonly description?: string;
}

interface StudioSelectMenuProps<TValue extends string> {
  readonly ariaLabel: string;
  readonly className?: string;
  readonly disabled?: boolean;
  readonly label: string;
  readonly onChange: (value: TValue) => void;
  readonly options: readonly StudioSelectOption<TValue>[];
  readonly title?: string;
  readonly value: TValue;
}

/**
 * An in-page listbox for the dense chapter-studio control strip.
 *
 * Native selects delegate their choices to the operating system, which makes
 * the available items invisible in captures and inconsistent with the rest of
 * the workspace. This control keeps the option list in the page while
 * retaining button/listbox semantics and keyboard dismissal.
 */
export function StudioSelectMenu<TValue extends string>({
  ariaLabel,
  className,
  disabled = false,
  label,
  onChange,
  options,
  title,
  value,
}: StudioSelectMenuProps<TValue>) {
  const [isOpen, setIsOpen] = useState(false);
  const menuId = useId();
  const rootRef = useRef<HTMLDivElement>(null);
  const selectedOption = options.find((option) => option.value === value) ?? options[0];

  useEffect(() => {
    if (!isOpen) return undefined;

    const closeWhenClickingOutside = (event: PointerEvent) => {
      if (event.target instanceof Node && !rootRef.current?.contains(event.target)) {
        setIsOpen(false);
      }
    };
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setIsOpen(false);
    };

    document.addEventListener("pointerdown", closeWhenClickingOutside);
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.removeEventListener("pointerdown", closeWhenClickingOutside);
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [isOpen]);

  return (
    <div
      className={className === undefined ? "studio-select-menu" : `studio-select-menu ${className}`}
      ref={rootRef}
    >
      <span className="studio-select-menu-label">{label}</span>
      <button
        aria-controls={menuId}
        aria-expanded={isOpen}
        aria-haspopup="listbox"
        aria-label={`${ariaLabel}：${selectedOption?.label ?? "未选择"}`}
        className="studio-select-menu-trigger"
        disabled={disabled}
        onClick={() => setIsOpen((open) => !open)}
        title={title}
        type="button"
      >
        <span>{selectedOption?.label ?? "未选择"}</span>
        <span aria-hidden="true" className="studio-select-menu-chevron">⌄</span>
      </button>
      {isOpen && (
        <div
          aria-label={`${ariaLabel}选项`}
          className="studio-select-menu-options"
          id={menuId}
          role="listbox"
        >
          {options.map((option) => {
            const isSelected = option.value === value;
            return (
              <button
                aria-selected={isSelected}
                className={isSelected ? "is-selected" : ""}
                key={option.value}
                onClick={() => {
                  onChange(option.value);
                  setIsOpen(false);
                }}
                role="option"
                type="button"
              >
                <span>{option.label}</span>
                {option.description !== undefined && (
                  <small>{option.description}</small>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

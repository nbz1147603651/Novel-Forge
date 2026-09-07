import { useCallback, useEffect, useId, useRef, useState } from "react";

export interface DropdownOption {
  readonly disabled?: boolean;
  readonly value: string;
  readonly label: string;
}

interface DropdownSelectProps {
  readonly ariaLabel: string;
  readonly className?: string;
  readonly disabled?: boolean;
  readonly onChange: (value: string) => void;
  readonly options: readonly DropdownOption[];
  readonly value: string;
}

/**
 * Custom dropdown that always opens its menu *below* the trigger element.
 *
 * Native `<select>` on macOS positions its popup via the window server which
 * frequently flips the list above the trigger when vertical space is tight.
 * This component replaces that behaviour with a CSS-positioned popover that
 * is anchored to `top: 100%` of the trigger wrapper.
 */
export function DropdownSelect({
  ariaLabel,
  className,
  disabled = false,
  onChange,
  options,
  value,
}: DropdownSelectProps) {
  const [open, setOpen] = useState(false);
  const [focusIndex, setFocusIndex] = useState(-1);
  const menuId = useId();
  const wrapperRef = useRef<HTMLDivElement>(null);
  const listRef = useRef<HTMLUListElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  const selectedLabel =
    options.find((o) => o.value === value)?.label ?? value;

  const close = useCallback(() => {
    setOpen(false);
    setFocusIndex(-1);
  }, []);

  const select = useCallback(
    (optValue: string, disabled = false) => {
      if (disabled) return;
      onChange(optValue);
      close();
      triggerRef.current?.focus();
    },
    [onChange, close],
  );

  // Close on outside click
  useEffect(() => {
    if (!open) return;
    const handler = (e: MouseEvent) => {
      if (
        wrapperRef.current &&
        !wrapperRef.current.contains(e.target as Node)
      ) {
        close();
      }
    };
    document.addEventListener("mousedown", handler);
    return () => document.removeEventListener("mousedown", handler);
  }, [open, close]);

  // Keyboard navigation
  const handleKeyDown = useCallback(
    (e: React.KeyboardEvent) => {
      if (!open) {
        if (e.key === "Enter" || e.key === " " || e.key === "ArrowDown") {
          e.preventDefault();
          setOpen(true);
          setFocusIndex(0);
        }
        return;
      }
      switch (e.key) {
        case "Escape":
          e.preventDefault();
          close();
          break;
        case "ArrowDown":
          e.preventDefault();
          setFocusIndex((i) => Math.min(i + 1, options.length - 1));
          break;
        case "ArrowUp":
          e.preventDefault();
          setFocusIndex((i) => Math.max(i - 1, 0));
          break;
        case "Enter":
        case " ":
          e.preventDefault();
          if (focusIndex >= 0 && focusIndex < options.length) {
            const option = options[focusIndex]!;
            select(option.value, option.disabled);
          }
          break;
        case "Home":
          e.preventDefault();
          setFocusIndex(0);
          break;
        case "End":
          e.preventDefault();
          setFocusIndex(options.length - 1);
          break;
        case "Tab":
          close();
          break;
      }
    },
    [open, close, focusIndex, options, select],
  );

  // Scroll focused item into view
  useEffect(() => {
    if (!open || focusIndex < 0 || !listRef.current) return;
    const item = listRef.current.children[focusIndex] as HTMLElement | undefined;
    item?.scrollIntoView({ block: "nearest" });
  }, [open, focusIndex]);

  const wrapperClass = [
    "dropdown-select",
    className,
    open ? "is-open" : "",
    disabled ? "is-disabled" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={wrapperClass}
      onKeyDown={handleKeyDown}
      ref={wrapperRef}
    >
      <button
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        aria-activedescendant={
          open && focusIndex >= 0 ? `${menuId}-option-${focusIndex}` : undefined
        }
        aria-haspopup="listbox"
        aria-label={ariaLabel}
        className="dropdown-select-trigger"
        disabled={disabled}
        onClick={() => {
          if (!disabled) {
            setOpen((v) => !v);
            if (!open) setFocusIndex(Math.max(0, options.findIndex((o) => o.value === value)));
          }
        }}
        ref={triggerRef}
        type="button"
      >
        <span className="dropdown-select-value">{selectedLabel}</span>
        <svg
          aria-hidden="true"
          className="dropdown-select-chevron"
          fill="none"
          height="12"
          stroke="currentColor"
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth="2"
          viewBox="0 0 24 24"
          width="12"
        >
          <path d="m6 9 6 6 6-6" />
        </svg>
      </button>
      {open && (
        <ul
          aria-label={ariaLabel}
          className="dropdown-select-menu"
          id={menuId}
          ref={listRef}
          role="listbox"
        >
          {options.map((option, index) => (
            <li
              aria-selected={option.value === value}
              aria-disabled={option.disabled || undefined}
              className={
                option.disabled
                  ? "dropdown-select-option is-disabled"
                  : option.value === value
                    ? "dropdown-select-option is-selected"
                  : index === focusIndex
                    ? "dropdown-select-option is-focused"
                    : "dropdown-select-option"
              }
              id={`${menuId}-option-${index}`}
              key={option.value}
              onClick={() => select(option.value, option.disabled)}
              onMouseEnter={() => {
                if (!option.disabled) setFocusIndex(index);
              }}
              role="option"
            >
              {option.value === value && (
                <span className="dropdown-select-check" aria-hidden="true">✓</span>
              )}
              <span>{option.label}</span>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

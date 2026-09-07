import { themeableBrandSymbolMarkup } from "../brand/symbol";

/** Renders the source SVG itself, recolored through the active semantic token. */
export function BrandLogo() {
  return <span aria-hidden="true" className="brand-symbol" dangerouslySetInnerHTML={{ __html: themeableBrandSymbolMarkup }} />;
}

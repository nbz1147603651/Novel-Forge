import sourceSvg from "../assets/nimo-symbol.svg?raw";

/** The shared, production NIMO symbol from the current PySide6 client. */
export function brandSymbolSvg(color: string): string {
  return sourceSvg.replaceAll("#000000", color);
}

export const themeableBrandSymbolMarkup = brandSymbolSvg("currentColor");

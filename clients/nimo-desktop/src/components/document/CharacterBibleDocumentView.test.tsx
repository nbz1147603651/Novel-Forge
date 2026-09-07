import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CharacterBibleDocumentView, characterDocumentData } from "./CharacterBibleDocumentView";
import { GenericReportView } from "./GenericReportView";

describe("character artifact documents", () => {
  it("keeps all characters and deduplicates bidirectional relationships", () => {
    const result = characterDocumentData(JSON.stringify({characters: [
      ...Array.from({length: 12}, (_, index) => ({character_id: `c${index}`, name: `人物${index}`, role: "supporting", relationships: {人物0: "同伴"}})),
      null, 1, {name: ""},
    ]}));
    expect(result.characters).toHaveLength(12);
    expect(result.relationships).toHaveLength(11);
    expect(result.characters[0]?.role).toBe("重要配角");
  });
  it("handles absent and invalid source data without demo fallbacks", () => {
    for (const content of [undefined, "bad json", "{}", '{"characters":{}}']) {
      expect(characterDocumentData(content).characters).toEqual([]);
      expect(renderToStaticMarkup(<CharacterBibleDocumentView content={content} />)).toContain("尚未写入");
    }
  });
  it("offers incremental expansion instead of a dead-end truncation notice", () => {
    const html = renderToStaticMarkup(<GenericReportView content={JSON.stringify({facts: Array.from({length: 12}, (_, index) => `事实${index}`)})} />);
    expect(html).toContain("展开更多（剩余 4 项）");
    expect(html).not.toContain("另有 4 项未展开");
  });
});

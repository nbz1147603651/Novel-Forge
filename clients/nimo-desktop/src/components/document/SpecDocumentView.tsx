import { useMemo } from "react";

import { formatLength, genreLabel, languageLabel, toneLabel } from "../../lib/document-labels";
import { DocH3, DocHintBlock, DocSection, DocTag, DocTagRow, DocText } from "./DocumentPrimitives";
import { num, parseJsonContent, str } from "./document-parse";

/**
 * Rich renderer for spec.json (故事规格) — port of PySide6 `render_spec`
 * (novel_forge/desktop/pages/document_renderer/reports/spec.py).
 *
 * Renders genre/tone/length/language badges, the theme overview section, and
 * the hint blocks (角色提示/世界观提示/冲突提示/视角提示/开篇风格/收束风格/附加指令).
 */
export function SpecDocumentView({ content }: { readonly content: string | undefined }) {
  const data = useMemo(() => parseJsonContent(content), [content]);

  if (data === null) return null;

  const genre = str(data, "genre");
  const tone = str(data, "tone");
  const length = num(data, "length_target");
  const language = str(data, "language") || "zh";
  const theme = str(data, "theme");

  const hintFields: readonly (readonly [string, string])[] = [
    ["characters_hint", "角色提示"],
    ["world_hint", "世界观提示"],
    ["conflict_hint", "冲突提示"],
    ["pov_hint", "视角提示"],
    ["opening_style", "开篇风格"],
    ["ending_style", "收束风格"],
    ["extra_instructions", "附加指令"],
  ];

  const hasBadges = genre.length > 0 || tone.length > 0 || length > 0 || language.length > 0;

  return (
    <div className="doc-rich">
      {hasBadges && (
        <DocTagRow>
          {genre.length > 0 && <DocTag>{genreLabel(genre)}</DocTag>}
          {tone.length > 0 && <DocTag>{toneLabel(tone)}</DocTag>}
          {length > 0 && <DocTag muted>{formatLength(length)}</DocTag>}
          {language.length > 0 && <DocTag muted>{languageLabel(language)}</DocTag>}
        </DocTagRow>
      )}

      {theme.length > 0 && (
        <DocSection title="主题概述">
          <DocText text={theme} />
        </DocSection>
      )}

      {hintFields.map(([key, label]) => {
        const value = str(data, key);
        if (value.length === 0) return null;
        return (
          <div key={key}>
            <DocH3>{label}</DocH3>
            <DocHintBlock>
              <DocText text={value} />
            </DocHintBlock>
          </div>
        );
      })}
    </div>
  );
}

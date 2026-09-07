import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StreamContent } from "./StreamContent";

describe("StreamContent outputKind override", () => {
  it("shows partial JSON as an unvalidated draft until the engine closes the stream", () => {
    const html = renderToStaticMarkup(
      <StreamContent outputKind="json" settled={false} text='{"街景":"骑楼、青石板' />,
    );
    expect(html).toContain("结构化结果生成中");
    expect(html).toContain("未校验草稿");
    expect(html).toContain("骑楼、青石板");
    expect(html).toContain("草稿不会写入正式结果");
    expect(html).not.toContain("json-tok-");
  });

  it("shows the latest partial JSON when a live draft exceeds the preview cap", () => {
    const html = renderToStaticMarkup(
      <StreamContent outputKind="json" settled={false} text={`{"开始":"${"甲".repeat(8_100)}","末尾":"仍在持续输出"`} />,
    );
    expect(html).toContain("已省略前");
    expect(html).toContain("仍在持续输出");
    expect(html.match(/已省略前/g)).toHaveLength(1);
    expect(html).toContain("显示最新 8,000 字符");
  });

  it("renders completed JSON as a field-oriented result with source disclosure", () => {
    const html = renderToStaticMarkup(
      <StreamContent outputKind="json" settled text='{"街景":"骑楼","线索":{"位置":"旧巷"}}' validationStatus="validated" />,
    );
    expect(html).toContain("结构化结果");
    expect(html).toContain("已校验");
    expect(html).toContain("街景");
    expect(html).toContain("骑楼");
    expect(html).toContain("查看完整 JSON 源码");
  });

  it("keeps an invalid completed fragment behind a repair disclosure", () => {
    const html = renderToStaticMarkup(
      <StreamContent outputKind="json" settled text='{"街景":"骑楼、青石板' validationStatus="failed" />,
    );
    expect(html).toContain("结构化结果未通过校验");
    expect(html).toContain("查看输出片段");
  });

  it("can reveal an invalid source immediately in the prominent task view", () => {
    const html = renderToStaticMarkup(
      <StreamContent
        outputKind="json"
        revealInvalidSource
        settled
        text='{"街景":"骑楼、青石板'
        validationStatus="failed"
      />,
    );
    expect(html).toContain('<details class="stream-json-source-fold" open="">');
    expect(html).toContain("骑楼、青石板");
  });

  it("does not present syntactically complete JSON as validated without a backend verdict", () => {
    const html = renderToStaticMarkup(
      <StreamContent outputKind="json" settled text='{"裁定":"待定"}' />,
    );
    expect(html).toContain("结构化结果待核验");
    expect(html).not.toContain('stream-json-result-status">已校验');
  });

  it("shows the backend validation phase instead of claiming generation is still active", () => {
    const html = renderToStaticMarkup(
      <StreamContent outputKind="json" settled={false} text='{"裁定":"通过"}' validationStatus="validating" />,
    );
    expect(html).toContain("结构化结果校验中");
    expect(html).toContain("字段与语义校验");
    expect(html).not.toContain("结构化结果生成中");
  });

  it("shows a validated clipped snapshot without parsing it as broken JSON", () => {
    const html = renderToStaticMarkup(
      <StreamContent
        outputKind="json"
        settled
        text='{"summary":"截取预览'
        textLength={23_938}
        textTruncated
        validationStatus="validated"
      />,
    );
    expect(html).toContain("结构化结果已校验");
    expect(html).toContain("完整长度 23,938 字符");
    expect(html).toContain("当前预览");
    expect(html).not.toContain("结构化结果尚未通过校验");
  });

  it("declared text outputKind renders prose without detection", () => {
    const html = renderToStaticMarkup(
      <StreamContent outputKind="text" text="灰瓦在雨里发亮。" />,
    );
    expect(html).toContain("灰瓦在雨里发亮");
    expect(html).not.toContain("JSON 片段");
  });

  it("falls back to detection when outputKind is absent", () => {
    const html = renderToStaticMarkup(<StreamContent settled={false} text='{"a":"b' />);
    expect(html).toContain("结构化结果生成中");
  });

  it("renders the truncation notice only once", () => {
    const html = renderToStaticMarkup(
      <StreamContent density="preview" outputKind="text" text={"长篇正文".repeat(300)} />,
    );
    expect(html.match(/已截断，仅显示前部预览/g)).toHaveLength(1);
  });

  it("keeps the latest live prose visible without splitting Unicode characters", () => {
    const html = renderToStaticMarkup(
      <StreamContent live outputKind="text" settled={false} text={`旧开头${"雨🌧".repeat(4_100)}最新落笔`} />,
    );
    expect(html).toContain("最新落笔");
    expect(html).not.toContain("旧开头");
    expect(html).not.toContain("�");
    expect(html.match(/已省略前/g)).toHaveLength(1);
  });
});

import { Divider, Grid, H1, H2, H3, Stack, Stat, Table, Text, Callout, Tag } from 'qoder/canvas';

export default function EnglishPipelineSummary() {
  return (
    <Stack gap={20}>
      <H1>English Novel Generation Pipeline</H1>
      <Text tone="secondary">Complete Implementation Summary</Text>

      <Grid columns={4} gap={16}>
        <Stat value="144" label="Templates Translated" tone="success" />
        <Stat value="0" label="Chinese Lines Remaining" tone="success" />
        <Stat value="25" label="Humanize Rules (EN)" />
        <Stat value="8" label="Western Elements" />
      </Grid>

      <Divider />

      <H2>Implementation Overview</H2>

      <Stack gap={12}>
        <Callout tone="success">
          <Text weight="medium">All 6 requirements completed and verified</Text>
          <Text size="small">The English novel generation pipeline is now fully operational with end-to-end support.</Text>
        </Callout>

        <Table
          headers={['Requirement', 'Status', 'Key Deliverables']}
          rows={[
            ['1. EN Prompt Templates', 'Complete', '144 .j2 files translated, 0 Chinese prose lines remaining'],
            ['2. Pipeline Code i18n', 'Complete', 'Language-aware messages in draft.py, edit.py, continuity_repair.py, finalize_report.py'],
            ['3. Humanize Library', 'Complete', 'from_language() method added, humanize_library_en.db with 25 rules'],
            ['4. Element Library', 'Complete', '8 Western literary elements (Chosen One, Unreliable Narrator, etc.)'],
            ['5. Regression Tests', 'Complete', '588 tests passed, all verification scripts PASS'],
            ['6. End-to-End Verification', 'Complete', 'All key tasks load correctly for both en and zh locales'],
          ]}
          rowTone={['success', 'success', 'success', 'success', 'success', 'success']}
        />
      </Stack>

      <Divider />

      <H2>Key Technical Achievements</H2>

      <Grid columns={2} gap={16}>
        <Stack gap={8}>
          <H3>Prompt Translation</H3>
          <Text size="small">
            Used MiniMax API for batch translation with iterative refinement.
            Preserved all Jinja2 syntax, variable names, and field names.
            Translated from 13,000+ Chinese lines to 0 prose Chinese lines.
          </Text>
        </Stack>

        <Stack gap={8}>
          <H3>Language-Aware Pipeline</H3>
          <Text size="small">
            Added _is_en_language() helper and language-specific string dictionaries.
            Messages dynamically switch between English and Chinese based on spec.language.
            Zero regression in Chinese pipeline behavior.
          </Text>
        </Stack>

        <Stack gap={8}>
          <H3>Humanize Library</H3>
          <Text size="small">
            Created humanize_library_en.db with 25 English AI-ism detection rules.
            Added from_language() factory method for automatic DB selection.
            Integrated into humanize_layer.py for language-aware loading.
          </Text>
        </Stack>

        <Stack gap={8}>
          <H3>Western Literary Elements</H3>
          <Text size="small">
            Added 8欧美化 narrative elements: Chosen One Arc, Unreliable Narrator,
            Redemption Arc, Found Family, Moral Ambiguity, etc.
            Coexists with existing Chinese elements without conflicts.
          </Text>
        </Stack>
      </Grid>

      <Divider />

      <H2>Verification Results</H2>

      <Table
        headers={['Verification', 'Result', 'Details']}
        rows={[
          ['verify_templates.py', 'PASS', '144 templates syntax-valid'],
          ['lint_prompt_layers.py', 'PASS', 'All layers maintainable'],
          ['verify_format_contracts.py', 'PASS', 'Schema-first contracts renderable'],
          ['Unit Tests', 'PASS', '588 passed, 24 skipped'],
          ['EN Template Loading', 'PASS', 'All 9 key tasks load successfully'],
          ['Humanize Library EN', 'PASS', '25 entries, 25 regex rules'],
          ['Element Library', 'PASS', '8 elements, valid JSON'],
          ['Chinese Prose Lines', 'PASS', '0 remaining in EN templates'],
        ]}
        rowTone={['success', 'success', 'success', 'success', 'success', 'success', 'success', 'success']}
      />

      <Divider />

      <H2>Modified Files Summary</H2>

      <Stack gap={8}>
        <Text weight="medium">Core Pipeline Changes:</Text>
        <Text size="small">• novel_forge/pipeline/short/stages/draft.py - Language-aware segment bridge messages</Text>
        <Text size="small">• novel_forge/pipeline/short/stages/edit.py - Language-aware completeness checks</Text>
        <Text size="small">• novel_forge/pipeline/long/stages/continuity_repair.py - Language-aware escalation notes</Text>
        <Text size="small">• novel_forge/pipeline/long/stages/finalize_report.py - English prompt leak keywords</Text>
        <Text size="small">• novel_forge/pipeline/long/stages/humanize_layer.py - Language-aware library loading</Text>
      </Stack>

      <Stack gap={8}>
        <Text weight="medium">Memory & Library Changes:</Text>
        <Text size="small">• novel_forge/memory/humanize_library_store.py - from_language() factory method</Text>
        <Text size="small">• data/_global/humanize_library/humanize_library_en.db - 25 English AI-ism rules</Text>
        <Text size="small">• data/element_library.json - 8 Western literary elements</Text>
      </Stack>

      <Stack gap={8}>
        <Text weight="medium">Prompt Pack Changes:</Text>
        <Text size="small">• novel_forge/prompts/packs/en/manifest.toml - status=stable</Text>
        <Text size="small">• novel_forge/prompts/packs/en/templates/ - 144 fully translated .j2 files</Text>
      </Stack>

      <Divider />

      <Callout tone="info">
        <Text weight="medium">End-to-End Capability</Text>
        <Text size="small">
          When spec.language="en": System loads EN prompt pack, English Humanize DB, Western elements,
          and generates English novels with Western literary conventions.
        </Text>
        <Text size="small">
          When spec.language="zh": All behavior remains identical to pre-modification state.
        </Text>
      </Callout>

      <Text tone="secondary" size="small">
        Generated for Qoder Quest - English Novel Generation Pipeline Implementation
      </Text>
    </Stack>
  );
}

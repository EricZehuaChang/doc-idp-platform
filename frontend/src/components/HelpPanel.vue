<template>
  <!-- help entry (M2 platform item): quick-start + shortcut reference -->
  <div class="mask" @click.self="$emit('close')">
    <div class="panel">
      <header>
        <strong>{{ $t('helpPanel.help') }}</strong>
        <button @click="$emit('close')">✕</button>
      </header>
      <section>
        <h3>{{ $t('helpPanel.basic_workflow') }}</h3>
        <ol>
          <li>{{ $t('helpPanel.skill_center_define_fields_to_extract_and_publish_a') }}</li>
          <li>{{ $t('helpPanel.upload_documents_via_api_or_queue_the_platform_automatically') }}</li>
          <li>{{ $t('helpPanel.review_queue_low_confidence_files_go_to_manual_verification') }}</li>
          <li>{{ $t('helpPanel.verification_page_check_fields_box_select_to_locate_confirm') }}</li>
          <li>{{ $t('helpPanel.data_cabinet_browse_approved_data_and_export_csv') }}</li>
        </ol>
      </section>
      <section>
        <h3>{{ $t('helpPanel.review_shortcuts') }}</h3>
        <table>
          <tbody>
            <tr><td><kbd>J</kbd> / <kbd>K</kbd></td><td>{{ $t('helpPanel.next_previous_field') }}</td></tr>
            <tr><td><kbd>Enter</kbd> / <kbd>Esc</kbd></td><td>{{ $t('helpPanel.edit_field_exit_editing') }}</td></tr>
            <tr><td><kbd>T</kbd></td><td>{{ $t('helpPanel.switch_all_to_review_tab') }}</td></tr>
            <tr><td><kbd>B</kbd></td><td>{{ $t('helpPanel.box_selection_annotation_mode') }}</td></tr>
            <tr><td><kbd>L</kbd></td><td>{{ $t('helpPanel.lock_and_start_validation') }}</td></tr>
            <tr><td><kbd>S</kbd> / <kbd>Ctrl+S</kbd></td><td>{{ $t('helpPanel.save_corrections') }}</td></tr>
            <tr><td><kbd>C</kbd> / <kbd>X</kbd></td><td>{{ $t('helpPanel.confirm_pass_reject') }}</td></tr>
            <tr><td><kbd>←</kbd> / <kbd>→</kbd></td><td>{{ $t('helpPanel.previous_next_document') }}</td></tr>
          </tbody>
        </table>
      </section>
      <section>
        <h3>{{ $t('helpPanel.field_marker') }}</h3>
        <I18nT keypath="helpPanel.field_marker_legend" tag="p" scope="global">
          <template #review><span class="badge-r">R</span></template>
          <template #inferred><span class="badge-inferred">{{ $t('helpPanel.inferred_result') }}</span></template>
          <template #confidence><code>c0-c3</code></template>
        </I18nT>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
import { I18nT } from "vue-i18n";
defineEmits<{ (e: "close"): void }>();
</script>

<style scoped>
.mask { position: fixed; inset: 0; background: rgba(0, 0, 0, 0.5); z-index: 100;
  display: flex; justify-content: flex-end; }
.panel { width: 380px; max-width: 90vw; background: var(--bg-panel); height: 100%;
  overflow-y: auto; padding: 18px; border-left: 1px solid var(--border); }
header { display: flex; justify-content: space-between; align-items: center;
  margin-bottom: 12px; }
h3 { font-size: 14px; color: var(--accent); margin: 16px 0 8px; }
ol { padding-left: 20px; line-height: 1.9; font-size: 13px; }
table { font-size: 13px; border-collapse: collapse; width: 100%; }
td { padding: 5px 8px; border-bottom: 1px solid var(--border); }
kbd { background: var(--bg-raised); border: 1px solid var(--border); border-radius: 4px;
  padding: 1px 6px; font-size: 12px; }
p { font-size: 13px; line-height: 2; }
</style>

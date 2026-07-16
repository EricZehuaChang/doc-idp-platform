<template>
  <!-- help entry (M2 platform item): quick-start + shortcut reference -->
  <div class="mask" @click.self="$emit('close')">
    <div class="panel">
      <header>
        <strong>帮助</strong>
        <button @click="$emit('close')">✕</button>
      </header>
      <section>
        <h3>基本流程</h3>
        <ol>
          <li>技能中心：定义要抽取的字段并发布技能版本</li>
          <li>通过 API 或队列上传文档，平台自动解析并抽取</li>
          <li>待审队列：低置信文件进入人工校验</li>
          <li>校验页：核对字段、框选定位、确认或拒绝</li>
          <li>数据柜：已通过数据可浏览与导出 CSV</li>
        </ol>
      </section>
      <section>
        <h3>审单快捷键</h3>
        <table>
          <tbody>
            <tr><td><kbd>J</kbd> / <kbd>K</kbd></td><td>下一个 / 上一个字段</td></tr>
            <tr><td><kbd>Enter</kbd> / <kbd>Esc</kbd></td><td>编辑字段 / 退出编辑</td></tr>
            <tr><td><kbd>T</kbd></td><td>切换 全部/待复核 页签</td></tr>
            <tr><td><kbd>B</kbd></td><td>框选标注模式</td></tr>
            <tr><td><kbd>L</kbd></td><td>锁定开始校验</td></tr>
            <tr><td><kbd>S</kbd> / <kbd>Ctrl+S</kbd></td><td>保存修正</td></tr>
            <tr><td><kbd>C</kbd> / <kbd>X</kbd></td><td>确认通过 / 拒绝</td></tr>
            <tr><td><kbd>←</kbd> / <kbd>→</kbd></td><td>上一份 / 下一份文档</td></tr>
          </tbody>
        </table>
      </section>
      <section>
        <h3>字段标记</h3>
        <p><span class="badge-r">R</span> 需人工复核
           <span class="badge-inferred">参考结果</span> 模型推断值（非原文抄录）
           <code>c0-c3</code> 置信等级</p>
      </section>
    </div>
  </div>
</template>

<script setup lang="ts">
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

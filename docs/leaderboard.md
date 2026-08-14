---
title: "AI 论文引用排行榜 · Semantic Scholar Citation Leaderboard"
description: "Paper Notes 收录论文的 Semantic Scholar 引用排行榜，可按会议、年份和研究领域筛选，并查看总引用与高影响力引用。"
tags:
  - "论文引用排行榜"
  - "Semantic Scholar"
  - "Citation Leaderboard"
search:
  exclude: true
hide:
  - toc
  - navigation
---

# 🏆 AI 论文引用排行榜

基于 [Semantic Scholar](https://www.semanticscholar.org/) 的公开引用数据，按论文去重并定期更新。可按会议、年份、研究领域或标题筛选；点击论文标题可直接阅读站内解读。

<div id="citation-leaderboard" data-source="../assets/data/citations.json">

<div class="cl-stats" aria-label="排行榜统计">
  <div class="cl-stat"><span data-cl-stat="works">—</span><small>已匹配论文</small></div>
  <div class="cl-stat"><span data-cl-stat="citations">—</span><small>引用总数</small></div>
  <div class="cl-stat"><span data-cl-stat="influential">—</span><small>高影响力引用</small></div>
  <div class="cl-stat"><span data-cl-stat="coverage">—</span><small>笔记覆盖率</small></div>
  <div class="cl-stat"><span data-cl-stat="updated">—</span><small>数据更新时间</small></div>
</div>

<div class="cl-controls" role="search" aria-label="筛选引用排行榜">
  <label class="cl-search-field">
    <span>搜索论文</span>
    <input id="cl-search" type="search" autocomplete="off" autocapitalize="off" spellcheck="false" placeholder="输入标题关键词…">
  </label>
  <label>
    <span>会议</span>
    <select id="cl-conference"><option value="">全部会议</option></select>
  </label>
  <label>
    <span>年份</span>
    <select id="cl-year"><option value="">全部年份</option></select>
  </label>
  <label>
    <span>研究领域</span>
    <select id="cl-area"><option value="">全部领域</option></select>
  </label>
  <label>
    <span>排序</span>
    <select id="cl-sort">
      <option value="citations">总引用（高 → 低）</option>
      <option value="influential">高影响力引用（高 → 低）</option>
      <option value="newest">发表年份（新 → 旧）</option>
      <option value="title">标题（A → Z）</option>
    </select>
  </label>
  <label class="cl-checkbox"><input id="cl-cited-only" type="checkbox"><span>仅显示已有引用</span></label>
  <button id="cl-clear" type="button">重置</button>
</div>

<div class="cl-result-bar">
  <span id="cl-status" role="status" aria-live="polite">正在加载 Semantic Scholar 引用数据…</span>
  <span class="cl-source-note">引用数以 Semantic Scholar 当前记录为准</span>
</div>

<div class="cl-table-wrap">
<table class="cl-table">
  <thead>
    <tr>
      <th scope="col">排名</th>
      <th scope="col">论文</th>
      <th scope="col">会议</th>
      <th scope="col">领域</th>
      <th scope="col">引用 / 高影响力</th>
      <th scope="col">来源</th>
    </tr>
  </thead>
  <tbody id="cl-rows">
    <tr><td class="cl-empty" colspan="6">正在加载…</td></tr>
  </tbody>
</table>
</div>

<nav class="cl-pagination" aria-label="排行榜分页">
  <button id="cl-prev" type="button">← 上一页</button>
  <span id="cl-page-label">第 1 页</span>
  <button id="cl-next" type="button">下一页 →</button>
</nav>

<noscript><p class="cl-noscript">需要启用 JavaScript 才能查看和筛选排行榜。</p></noscript>

</div>

## 数据口径

- 总引用取自 Semantic Scholar 的 `citationCount`；「高影响力引用」取自 `influentialCitationCount`，是其算法判定对施引论文有显著影响的引用子集。不同学术数据库的收录范围与合并规则不同，数字不会完全一致。
- 优先通过 DOI、ACL Anthology DOI 与 arXiv ID 精确查询；每个结果还会经过严格标题核验，未带标识符的笔记才使用标题匹配。同一 Semantic Scholar 论文的多篇站内笔记只计一次。
- Semantic Scholar 尚未收录或无法可靠匹配的论文不会进入排名，页面上的「笔记覆盖率」会公开显示当前匹配范围。自动更新可使用无需密钥的公开 API；遇到共享限流时会保留缓存并在下次继续。

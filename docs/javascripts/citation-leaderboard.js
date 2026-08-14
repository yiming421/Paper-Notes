/* Interactive Semantic Scholar citation leaderboard for /leaderboard/.
 *
 * The generated JSON is loaded only on the leaderboard page. Material's
 * instant navigation is supported through document$.
 */
(function () {
  "use strict";

  var PAGE_SIZE = 50;
  var dataPromises = Object.create(null);
  var numberFormat = new Intl.NumberFormat("zh-CN");
  var dateFormat = new Intl.DateTimeFormat("zh-CN", {
    year: "numeric",
    month: "short",
    day: "numeric"
  });

  var AREA_LABELS = {
    "3d_vision": "3D 视觉",
    ai_safety: "AI 安全",
    aigc_detection: "AIGC 检测",
    anomaly_detection: "异常检测",
    audio_speech: "音频 / 语音",
    autonomous_driving: "自动驾驶",
    causal_inference: "因果推理",
    code_intelligence: "代码智能",
    computational_biology: "计算生物",
    dialogue: "对话系统",
    earth_science: "地球科学",
    federated_learning: "联邦学习",
    graph_learning: "图学习",
    hallucination: "幻觉检测",
    human_understanding: "人体理解",
    image_generation: "图像生成",
    image_restoration: "图像恢复",
    information_retrieval: "信息检索 / RAG",
    interpretability: "可解释性",
    knowledge_editing: "知识编辑",
    learning_theory: "学习理论",
    llm_agent: "LLM Agent",
    llm_alignment: "对齐 / RLHF",
    llm_efficiency: "LLM 效率",
    llm_evaluation: "LLM 评测",
    llm_nlp: "LLM 其他",
    llm_pretraining: "预训练",
    llm_reasoning: "LLM Reasoning",
    llm_safety: "LLM 安全",
    medical_imaging: "医学图像",
    medical_nlp: "医疗 NLP",
    model_compression: "模型压缩",
    multi_agent: "Multi-Agent",
    multilingual_mt: "多语言 / 翻译",
    multimodal_vlm: "多模态 VLM",
    nlp_generation: "文本生成",
    nlp_understanding: "NLP 理解",
    object_detection: "目标检测",
    optimization: "优化 / 理论",
    others: "其他",
    physics: "物理 / 科学计算",
    recommender: "推荐系统",
    reinforcement_learning: "强化学习",
    remote_sensing: "遥感",
    robotics: "机器人 / 具身智能",
    segmentation: "语义分割",
    self_supervised: "自监督 / 表示学习",
    signal_comm: "信号 / 通信",
    social_computing: "社会计算",
    time_series: "时间序列",
    video_generation: "视频生成",
    video_understanding: "视频理解",
    vlm_efficiency: "VLM Efficiency",
    vlm_reasoning: "VLM Reasoning"
  };

  function prettyArea(value) {
    if (AREA_LABELS[value]) return AREA_LABELS[value];
    return value.replace(/[_-]+/g, " ").replace(/\b\w/g, function (character) {
      return character.toUpperCase();
    });
  }

  function prettyConference(value) {
    return value.replace(/(\D)(\d{4})$/, "$1 $2");
  }

  function conferenceName(value) {
    return value.replace(/\d{4}$/, "");
  }

  function conferenceYear(value) {
    var match = value.match(/(\d{4})$/);
    return match ? match[1] : "";
  }

  function normalize(value) {
    return (value || "")
      .toLocaleLowerCase()
      .normalize("NFKC")
      .replace(/[^\p{L}\p{N}]+/gu, " ")
      .trim();
  }

  function loadData(url) {
    if (!dataPromises[url]) {
      dataPromises[url] = fetch(url, { credentials: "same-origin" }).then(function (response) {
        if (!response.ok) throw new Error("HTTP " + response.status);
        return response.json();
      });
    }
    return dataPromises[url];
  }

  function setText(root, selector, value) {
    var element = root.querySelector(selector);
    if (element) element.textContent = value;
  }

  function fillSelect(select, values, allLabel, formatter) {
    var selected = select.value;
    select.innerHTML = "";
    var all = document.createElement("option");
    all.value = "";
    all.textContent = allLabel;
    select.appendChild(all);
    values.forEach(function (value) {
      var option = document.createElement("option");
      option.value = value;
      option.textContent = formatter(value);
      select.appendChild(option);
    });
    if (values.indexOf(selected) >= 0) select.value = selected;
  }

  function distinctNotes(papers, conference, year) {
    var seen = Object.create(null);
    papers.forEach(function (paper) {
      paper.notes.forEach(function (note) {
        if (
          (!conference || conferenceName(note.conference) === conference) &&
          (!year || conferenceYear(note.conference) === year)
        ) {
          seen[note.area] = true;
        }
      });
    });
    return Object.keys(seen).sort(function (a, b) {
      return prettyArea(a).localeCompare(prettyArea(b), "zh-CN");
    });
  }

  function matchingNotes(paper, conference, year, area) {
    return paper.notes.filter(function (note) {
      return (
        (!conference || conferenceName(note.conference) === conference) &&
        (!year || conferenceYear(note.conference) === year) &&
        (!area || note.area === area)
      );
    });
  }

  function noteHref(note) {
    return new URL("../" + note.path.replace(/^\/+/, ""), location.href).href;
  }

  function appendTextCell(row, className, value) {
    var cell = document.createElement("td");
    cell.className = className;
    cell.textContent = value;
    row.appendChild(cell);
    return cell;
  }

  function makePaperCell(paper, notes) {
    var cell = document.createElement("td");
    cell.className = "cl-paper";
    var link = document.createElement("a");
    link.className = "cl-paper-title";
    link.href = noteHref(notes[0] || paper.notes[0]);
    link.textContent = paper.title;
    cell.appendChild(link);

    var meta = document.createElement("div");
    meta.className = "cl-paper-meta";
    if (paper.year) {
      var year = document.createElement("span");
      year.textContent = paper.year;
      meta.appendChild(year);
    }
    if (paper.notes.length > 1) {
      var noteCount = document.createElement("span");
      noteCount.textContent = paper.notes.length + " 篇站内笔记";
      meta.appendChild(noteCount);
    }
    if (meta.childNodes.length) cell.appendChild(meta);
    return cell;
  }

  function makeVenueCell(notes) {
    var cell = document.createElement("td");
    cell.className = "cl-venue";
    var seen = Object.create(null);
    notes.forEach(function (note) {
      if (seen[note.conference]) return;
      seen[note.conference] = true;
      var badge = document.createElement("span");
      badge.className = "cl-badge";
      badge.textContent = prettyConference(note.conference);
      cell.appendChild(badge);
    });
    return cell;
  }

  function rankLabel(rank) {
    if (rank === 1) return "🥇";
    if (rank === 2) return "🥈";
    if (rank === 3) return "🥉";
    return String(rank);
  }

  function renderRows(tbody, papers, start, conference, year, area) {
    tbody.innerHTML = "";
    papers.forEach(function (paper, index) {
      var notes = matchingNotes(paper, conference, year, area);
      var row = document.createElement("tr");
      appendTextCell(row, "cl-rank", rankLabel(start + index + 1));
      row.appendChild(makePaperCell(paper, notes));
      row.appendChild(makeVenueCell(notes));

      var areas = [];
      var seenAreas = Object.create(null);
      notes.forEach(function (note) {
        if (!seenAreas[note.area]) {
          seenAreas[note.area] = true;
          areas.push(prettyArea(note.area));
        }
      });
      appendTextCell(row, "cl-area", areas.join(" · "));

      var citationCell = appendTextCell(
        row,
        "cl-citations",
        numberFormat.format(paper.citations)
      );
      var influential = document.createElement("small");
      influential.textContent =
        "高影响 " + numberFormat.format(paper.influential_citations || 0);
      citationCell.appendChild(influential);
      citationCell.title =
        "Semantic Scholar 高影响力引用 " +
        numberFormat.format(paper.influential_citations || 0) +
        " 次";

      var sourceCell = document.createElement("td");
      sourceCell.className = "cl-source";
      var sourceLink = document.createElement("a");
      sourceLink.href = paper.semantic_scholar_url;
      sourceLink.target = "_blank";
      sourceLink.rel = "noopener";
      sourceLink.textContent = "S2 ↗";
      sourceCell.appendChild(sourceLink);
      row.appendChild(sourceCell);
      tbody.appendChild(row);
    });
  }

  function sortPapers(papers, mode) {
    papers.sort(function (a, b) {
      if (mode === "influential") {
        return (
          (b.influential_citations || 0) - (a.influential_citations || 0) ||
          b.citations - a.citations ||
          a.title.localeCompare(b.title)
        );
      }
      if (mode === "newest") {
        return (
          (b.year || 0) - (a.year || 0) ||
          b.citations - a.citations ||
          a.title.localeCompare(b.title)
        );
      }
      if (mode === "title") return a.title.localeCompare(b.title);
      return (
        b.citations - a.citations ||
        (b.influential_citations || 0) - (a.influential_citations || 0) ||
        a.title.localeCompare(b.title)
      );
    });
    return papers;
  }

  function queryState() {
    var params = new URLSearchParams(location.search);
    return {
      query: params.get("q") || "",
      conference: params.get("conference") || "",
      year: params.get("year") || "",
      area: params.get("area") || "",
      sort: params.get("sort") || "citations",
      page: Math.max(1, parseInt(params.get("page"), 10) || 1)
    };
  }

  function writeQueryState(state) {
    var url = new URL(location.href);
    ["q", "conference", "year", "area", "sort", "page"].forEach(function (key) {
      url.searchParams.delete(key);
    });
    if (state.query) url.searchParams.set("q", state.query);
    if (state.conference) url.searchParams.set("conference", state.conference);
    if (state.year) url.searchParams.set("year", state.year);
    if (state.area) url.searchParams.set("area", state.area);
    if (state.sort && state.sort !== "citations") url.searchParams.set("sort", state.sort);
    if (state.page > 1) url.searchParams.set("page", String(state.page));
    history.replaceState(history.state, "", url.pathname + url.search + url.hash);
  }

  function initLeaderboard(root, data) {
    if (root.dataset.ready === "true") return;
    root.dataset.ready = "true";

    var papers = Array.isArray(data.papers) ? data.papers : [];
    var search = root.querySelector("#cl-search");
    var conference = root.querySelector("#cl-conference");
    var year = root.querySelector("#cl-year");
    var area = root.querySelector("#cl-area");
    var sort = root.querySelector("#cl-sort");
    var citedOnly = root.querySelector("#cl-cited-only");
    var status = root.querySelector("#cl-status");
    var tbody = root.querySelector("#cl-rows");
    var previous = root.querySelector("#cl-prev");
    var next = root.querySelector("#cl-next");
    var pageLabel = root.querySelector("#cl-page-label");
    var clear = root.querySelector("#cl-clear");
    if (!search || !conference || !year || !area || !sort || !tbody) return;

    setText(root, "[data-cl-stat='works']", numberFormat.format(data.matched_works || 0));
    setText(root, "[data-cl-stat='citations']", numberFormat.format(data.total_citations || 0));
    setText(
      root,
      "[data-cl-stat='influential']",
      numberFormat.format(data.total_influential_citations || 0)
    );
    var coverage = data.total_notes
      ? Math.round((1000 * data.matched_notes) / data.total_notes) / 10
      : 0;
    setText(root, "[data-cl-stat='coverage']", coverage + "%");
    try {
      setText(root, "[data-cl-stat='updated']", dateFormat.format(new Date(data.generated_at)));
    } catch (error) {
      setText(root, "[data-cl-stat='updated']", data.generated_at || "—");
    }

    var conferences = Object.create(null);
    var years = Object.create(null);
    papers.forEach(function (paper) {
      paper.notes.forEach(function (note) {
        conferences[conferenceName(note.conference)] = true;
        var noteYear = conferenceYear(note.conference);
        if (noteYear) years[noteYear] = true;
      });
    });
    fillSelect(
      conference,
      Object.keys(conferences).sort(function (a, b) {
        return a.localeCompare(b);
      }),
      "全部会议",
      function (value) { return value; }
    );
    fillSelect(
      year,
      Object.keys(years).sort(function (a, b) {
        return parseInt(b, 10) - parseInt(a, 10);
      }),
      "全部年份",
      function (value) { return value; }
    );

    var initial = queryState();
    search.value = initial.query;
    var initialConference = initial.conference;
    var initialYear = initial.year;
    if (!Object.prototype.hasOwnProperty.call(conferences, initialConference)) {
      var legacyConference = conferenceName(initialConference);
      if (Object.prototype.hasOwnProperty.call(conferences, legacyConference)) {
        initialConference = legacyConference;
        if (!initialYear) initialYear = conferenceYear(initial.conference);
      }
    }
    if (Object.prototype.hasOwnProperty.call(conferences, initialConference)) {
      conference.value = initialConference;
    }
    if (Object.prototype.hasOwnProperty.call(years, initialYear)) {
      year.value = initialYear;
    }
    fillSelect(
      area,
      distinctNotes(papers, conference.value, year.value),
      "全部领域",
      prettyArea
    );
    if (Array.prototype.some.call(area.options, function (option) {
      return option.value === initial.area;
    })) {
      area.value = initial.area;
    }
    if (Array.prototype.some.call(sort.options, function (option) {
      return option.value === initial.sort;
    })) {
      sort.value = initial.sort;
    }
    var page = initial.page;

    function refreshAreas() {
      var previousArea = area.value;
      fillSelect(
        area,
        distinctNotes(papers, conference.value, year.value),
        "全部领域",
        prettyArea
      );
      if (Array.prototype.some.call(area.options, function (option) {
        return option.value === previousArea;
      })) {
        area.value = previousArea;
      }
    }

    function render() {
      var query = normalize(search.value);
      var selectedConference = conference.value;
      var selectedYear = year.value;
      var selectedArea = area.value;
      var filtered = papers.filter(function (paper) {
        var notes = matchingNotes(paper, selectedConference, selectedYear, selectedArea);
        if (!notes.length) return false;
        if (citedOnly.checked && paper.citations < 1) return false;
        if (!query) return true;
        var haystack = normalize(
          paper.title +
            " " +
            notes
              .map(function (note) {
                return note.conference + " " + prettyArea(note.area);
              })
              .join(" ")
        );
        return haystack.indexOf(query) >= 0;
      });
      sortPapers(filtered, sort.value);

      var totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
      page = Math.min(Math.max(1, page), totalPages);
      var start = (page - 1) * PAGE_SIZE;
      var visible = filtered.slice(start, start + PAGE_SIZE);
      renderRows(tbody, visible, start, selectedConference, selectedYear, selectedArea);

      if (!visible.length) {
        var emptyRow = document.createElement("tr");
        var emptyCell = document.createElement("td");
        emptyCell.colSpan = 6;
        emptyCell.className = "cl-empty";
        emptyCell.textContent = "没有符合当前筛选条件的论文。";
        emptyRow.appendChild(emptyCell);
        tbody.appendChild(emptyRow);
      }

      status.textContent =
        "共 " +
        numberFormat.format(filtered.length) +
        " 篇论文" +
        (filtered.length
          ? " · 显示第 " +
            numberFormat.format(start + 1) +
            "–" +
            numberFormat.format(Math.min(start + PAGE_SIZE, filtered.length)) +
            " 名"
          : "");
      pageLabel.textContent = "第 " + page + " / " + totalPages + " 页";
      previous.disabled = page <= 1;
      next.disabled = page >= totalPages;
      writeQueryState({
        query: search.value.trim(),
        conference: selectedConference,
        year: selectedYear,
        area: selectedArea,
        sort: sort.value,
        page: page
      });
    }

    var searchTimer = null;
    search.addEventListener("input", function () {
      page = 1;
      clearTimeout(searchTimer);
      searchTimer = setTimeout(render, 160);
    });
    conference.addEventListener("change", function () {
      page = 1;
      refreshAreas();
      render();
    });
    year.addEventListener("change", function () {
      page = 1;
      refreshAreas();
      render();
    });
    area.addEventListener("change", function () {
      page = 1;
      render();
    });
    sort.addEventListener("change", function () {
      page = 1;
      render();
    });
    citedOnly.addEventListener("change", function () {
      page = 1;
      render();
    });
    clear.addEventListener("click", function () {
      search.value = "";
      conference.value = "";
      year.value = "";
      refreshAreas();
      area.value = "";
      sort.value = "citations";
      citedOnly.checked = false;
      page = 1;
      render();
      search.focus();
    });
    previous.addEventListener("click", function () {
      if (page > 1) {
        page -= 1;
        render();
        status.scrollIntoView({ block: "start" });
      }
    });
    next.addEventListener("click", function () {
      page += 1;
      render();
      status.scrollIntoView({ block: "start" });
    });

    render();
  }

  function init() {
    var root = document.getElementById("citation-leaderboard");
    document.body.classList.toggle("pn-on-leaderboard", !!root);
    if (!root || root.dataset.loading === "true" || root.dataset.ready === "true") return;
    root.dataset.loading = "true";
    var source = root.getAttribute("data-source");
    var dataUrl = new URL(source, location.href).href;
    loadData(dataUrl)
      .then(function (data) {
        root.dataset.loading = "false";
        initLeaderboard(root, data);
      })
      .catch(function (error) {
        root.dataset.loading = "false";
        var status = root.querySelector("#cl-status");
        if (status) status.textContent = "引用数据加载失败，请稍后重试。";
        if (window.console) console.error("citation leaderboard:", error);
      });
  }

  if (window.document$ && typeof window.document$.subscribe === "function") {
    window.document$.subscribe(init);
  } else {
    document.addEventListener("DOMContentLoaded", init);
  }
})();

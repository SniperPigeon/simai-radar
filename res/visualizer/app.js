"use strict";

const DEFAULT_DISTRIBUTION_PERCENTILES = Object.freeze([30, 67, 85, 99]);
const PERCENTILE_PRECISION = 3;
const PERCENTILE_STEP = 10 ** -PERCENTILE_PRECISION;
const MAX_DISTRIBUTION_PERCENTILE = 100 - PERCENTILE_STEP;
const DISTRIBUTION_SCORE_ANCHORS = Object.freeze([50, 100, 150, 200]);

const state = {
  data: null,
  songs: [],
  charts: [],
  selectedSongId: null,
  selectedChartId: null,
  view: "library",
  search: "",
  kind: "all",
  difficulty: "all",
  rankingDimension: null,
  rankingKind: "all",
  rankingDifficulty: "all",
  visibleRanking: 100,
  distributionDimension: null,
  distributionKind: "all",
  distributionDifficulty: "all",
  distributionLevel: "all",
  distributionPercentilesByDimension: {},
  clientMappedDimensions: new Set(),
};

const elements = {
  appStatus: document.querySelector("#appStatus"),
  appShell: document.querySelector("#appShell"),
  librarySidebar: document.querySelector(".library-sidebar"),
  songCount: document.querySelector("#songCount"),
  chartCount: document.querySelector("#chartCount"),
  searchInput: document.querySelector("#searchInput"),
  kindFilter: document.querySelector("#kindFilter"),
  difficultyFilter: document.querySelector("#difficultyFilter"),
  resultCount: document.querySelector("#resultCount"),
  songList: document.querySelector("#songList"),
  detailView: document.querySelector("#detailView"),
  detailCover: document.querySelector("#detailCover"),
  songVersion: document.querySelector("#songVersion"),
  songTitle: document.querySelector("#songTitle"),
  songArtist: document.querySelector("#songArtist"),
  songFacts: document.querySelector("#songFacts"),
  chartCountLabel: document.querySelector("#chartCountLabel"),
  chartSwitcher: document.querySelector("#chartSwitcher"),
  chartMetadata: document.querySelector("#chartMetadata"),
  radarTitle: document.querySelector("#radarTitle"),
  radarChart: document.querySelector("#radarChart"),
  scoreBreakdown: document.querySelector("#scoreBreakdown"),
  rankingView: document.querySelector("#rankingView"),
  rankingSummary: document.querySelector("#rankingSummary"),
  rankingKindFilter: document.querySelector("#rankingKindFilter"),
  rankingDifficultyFilter: document.querySelector("#rankingDifficultyFilter"),
  dimensionTabs: document.querySelector("#dimensionTabs"),
  rankingList: document.querySelector("#rankingList"),
  loadMoreRanking: document.querySelector("#loadMoreRanking"),
  distributionView: document.querySelector("#distributionView"),
  distributionSummary: document.querySelector("#distributionSummary"),
  distributionKindFilter: document.querySelector("#distributionKindFilter"),
  distributionDifficultyFilter: document.querySelector("#distributionDifficultyFilter"),
  distributionLevelFilter: document.querySelector("#distributionLevelFilter"),
  distributionDimensionTabs: document.querySelector("#distributionDimensionTabs"),
  distributionStats: document.querySelector("#distributionStats"),
  exportMappingProfile: document.querySelector("#exportMappingProfile"),
  resetDistributionThresholds: document.querySelector("#resetDistributionThresholds"),
  thresholdControls: document.querySelector("#thresholdControls"),
  thresholdRows: document.querySelector("#thresholdRows"),
  distributionChartTitle: document.querySelector("#distributionChartTitle"),
  distributionChart: document.querySelector("#distributionChart"),
  rangeShare: document.querySelector("#rangeShare"),
};

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function normalized(value) {
  return String(value ?? "").normalize("NFKC").toLocaleLowerCase("zh-CN");
}

function scoreText(score) {
  return Number.isFinite(score) ? Number(score).toFixed(1) : "—";
}

function durationText(seconds) {
  if (!Number.isFinite(seconds)) return "—";
  const total = Math.max(0, Math.round(seconds));
  const minutes = Math.floor(total / 60);
  return `${minutes}:${String(total % 60).padStart(2, "0")}`;
}

function metadataText(value) {
  return value === null || value === undefined || value === "" ? "—" : String(value);
}

function kindText(kind) {
  if (kind === "DX") return "DX谱";
  if (kind === "ST") return "标谱";
  return kind || "类型未提供";
}

function difficultyText(value) {
  if (value === "all") return "全部难度";
  const match = state.charts.find(({ chart }) => String(chart.difficulty) === String(value));
  return match?.chart.difficultyLabel || String(value);
}

function percentile(sortedValues, percent) {
  if (!sortedValues.length) return 0;
  if (sortedValues.length === 1) return sortedValues[0];
  const position = (sortedValues.length - 1) * (percent / 100);
  const lower = Math.floor(position);
  const upper = Math.ceil(position);
  if (lower === upper) return sortedValues[lower];
  return sortedValues[lower] + (sortedValues[upper] - sortedValues[lower]) * (position - lower);
}

function rawValueText(value) {
  const number = Number(value) || 0;
  if (Math.abs(number) >= 10) return number.toFixed(3);
  return number.toFixed(4);
}

function percentileText(value) {
  return Number(value)
    .toFixed(PERCENTILE_PRECISION)
    .replace(/\.0+$/, "")
    .replace(/(\.\d*?)0+$/, "$1");
}

function distributionEntriesForDimension(dimensionKey) {
  return state.charts.filter(({ chart }) => {
    const rawValue = chart.rawScores?.[dimensionKey];
    return (
      Number.isFinite(rawValue) &&
      chartMatches(chart, state.distributionKind, state.distributionDifficulty) &&
      (state.distributionLevel === "all" || chart.level === state.distributionLevel)
    );
  });
}

function distributionEntries() {
  return distributionEntriesForDimension(state.distributionDimension);
}

function distributionValuesForDimension(dimensionKey) {
  return distributionEntriesForDimension(dimensionKey)
    .map(({ chart }) => chart.rawScores[dimensionKey])
    .sort((left, right) => left - right);
}

function distributionValues() {
  return distributionValuesForDimension(state.distributionDimension);
}

function currentDistributionPercentiles() {
  return state.distributionPercentilesByDimension[state.distributionDimension]
    || [...DEFAULT_DISTRIBUTION_PERCENTILES];
}

function mapRawScore(rawValue, rawThresholds) {
  if (!Number.isFinite(rawValue)) return null;
  if (rawValue <= 0) return 0;
  const rawAnchors = [0, ...rawThresholds];
  const scoreAnchors = [0, ...DISTRIBUTION_SCORE_ANCHORS];
  for (let index = 1; index < rawAnchors.length; index += 1) {
    const upperRaw = rawAnchors[index];
    if (rawValue > upperRaw) continue;
    const lowerRaw = rawAnchors[index - 1];
    const lowerScore = scoreAnchors[index - 1];
    const upperScore = scoreAnchors[index];
    if (upperRaw <= lowerRaw) return upperScore;
    return lowerScore + (
      (upperScore - lowerScore) * (rawValue - lowerRaw) / (upperRaw - lowerRaw)
    );
  }
  return DISTRIBUTION_SCORE_ANCHORS.at(-1);
}

function refreshDominantDimension(chart) {
  const available = state.data.dimensions.filter(
    (dimension) => Number.isFinite(chart.scores?.[dimension.key]),
  );
  chart.dominantDimension = available.length
    ? available.reduce((highest, dimension) =>
        chart.scores[dimension.key] > chart.scores[highest.key] ? dimension : highest,
      ).key
    : null;
}

function applyDistributionMapping(values) {
  const dimensionKey = state.distributionDimension;
  if (!state.clientMappedDimensions.has(dimensionKey) || !values.length) return;
  const rawThresholds = currentDistributionPercentiles().map(
    (percent) => percentile(values, percent),
  );
  state.charts.forEach(({ chart }) => {
    if (!chart.scores) chart.scores = {};
    chart.scores[dimensionKey] = mapRawScore(chart.rawScores?.[dimensionKey], rawThresholds);
    refreshDominantDimension(chart);
  });
}

function renderDistributionDimensionTabs() {
  elements.distributionDimensionTabs.innerHTML = state.data.dimensions
    .map(
      (dimension) => `
        <button
          class="dimension-tab ${dimension.key === state.distributionDimension ? "is-active" : ""}"
          type="button"
          role="tab"
          aria-selected="${dimension.key === state.distributionDimension}"
          data-distribution-dimension="${dimension.key}"
          style="--dimension-color:${dimension.color}"
        >${escapeHtml(dimension.label)}</button>`,
    )
    .join("");
}

function renderThresholdControls() {
  elements.thresholdControls.innerHTML = currentDistributionPercentiles()
    .map(
      (value, index) => `
        <label class="threshold-control" style="--threshold-color:var(--threshold-${index + 1})">
          <span class="threshold-control-heading">
            <strong>T${index + 1} · ${DISTRIBUTION_SCORE_ANCHORS[index]} 分</strong>
            <span>P<output data-threshold-percent-output="${index}">${value}</output></span>
          </span>
          <span class="threshold-control-inputs">
            <input type="range" min="1" max="${MAX_DISTRIBUTION_PERCENTILE}" step="${PERCENTILE_STEP}" value="${value}" data-threshold-index="${index}" />
            <input type="number" min="1" max="${MAX_DISTRIBUTION_PERCENTILE}" step="${PERCENTILE_STEP}" value="${value}" data-threshold-index="${index}" />
          </span>
          <span class="threshold-raw">raw <output data-threshold-raw="${index}">--</output></span>
        </label>`,
    )
    .join("");
}

function syncThresholdControls(values) {
  currentDistributionPercentiles().forEach((percent, index) => {
    document.querySelectorAll(`[data-threshold-index="${index}"]`).forEach((input) => {
      input.value = String(percent);
    });
    const percentOutput = document.querySelector(`[data-threshold-percent-output="${index}"]`);
    const rawOutput = document.querySelector(`[data-threshold-raw="${index}"]`);
    if (percentOutput) percentOutput.textContent = percentileText(percent);
    if (rawOutput) rawOutput.textContent = rawValueText(percentile(values, percent));
  });
}

function setDistributionPercentile(index, rawValue) {
  const value = Number(rawValue);
  if (!Number.isFinite(value)) return;
  const percentiles = currentDistributionPercentiles();
  const lower = index === 0 ? 1 : percentiles[index - 1] + PERCENTILE_STEP;
  const upper = index === percentiles.length - 1
    ? MAX_DISTRIBUTION_PERCENTILE
    : percentiles[index + 1] - PERCENTILE_STEP;
  const clamped = Math.min(upper, Math.max(lower, value));
  percentiles[index] = Number(clamped.toFixed(PERCENTILE_PRECISION));
  state.distributionPercentilesByDimension[state.distributionDimension] = percentiles;
  state.clientMappedDimensions.add(state.distributionDimension);
  renderDistributionResults();
}

function buildMappingProfile() {
  const generatedAt = new Date().toISOString();
  const dimensions = Object.fromEntries(state.data.dimensions.map((dimension) => {
    const values = distributionValuesForDimension(dimension.key);
    if (!values.length) throw new Error(`${dimension.label} 在当前筛选下没有 raw 数据`);
    const percentiles = state.distributionPercentilesByDimension[dimension.key];
    const rawAnchors = percentiles.map((percent) => percentile(values, percent));
    if (
      rawAnchors[0] <= 0
      || rawAnchors.some((value, index) => index > 0 && value <= rawAnchors[index - 1])
    ) {
      throw new Error(`${dimension.label} 的 T1–T4 raw 值必须严格递增且大于 0`);
    }
    return [dimension.key, {
      label: dimension.label,
      percentiles,
      rawAnchors,
      t4Max: values.at(-1),
      sampleCount: values.length,
    }];
  }));
  return {
    schemaVersion: "mairadar-mapping-profile-1",
    mappingVersion: `mapping-profile-${generatedAt.replaceAll(/[:.]/g, "-")}`,
    generatedAt,
    scoreAnchors: [...DISTRIBUTION_SCORE_ANCHORS],
    maximumScore: 220,
    calibrationFilters: {
      chartType: state.distributionKind,
      difficulty: state.distributionDifficulty,
      level: state.distributionLevel,
    },
    dimensions,
  };
}

function exportMappingProfile() {
  try {
    const profile = buildMappingProfile();
    const blob = new Blob([`${JSON.stringify(profile, null, 2)}\n`], {
      type: "application/json;charset=utf-8",
    });
    const url = URL.createObjectURL(blob);
    const link = document.createElement("a");
    link.href = url;
    link.download = "mapping_profile.json";
    link.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    window.alert(`无法导出 mapping_profile：${error.message}`);
  }
}

function distributionCurvePath(values, xScale, yScale) {
  return values
    .map((value, index) => {
      const percent = values.length === 1 ? 100 : (index / (values.length - 1)) * 100;
      return `${index === 0 ? "M" : "L"}${xScale(percent).toFixed(2)} ${yScale(value).toFixed(2)}`;
    })
    .join(" ");
}

function histogram(values, binCount, maximum) {
  const bins = Array.from({ length: binCount }, () => 0);
  const width = maximum > 0 ? maximum / binCount : 1;
  values.forEach((value) => {
    const index = Math.min(binCount - 1, Math.floor(value / width));
    bins[Math.max(0, index)] += 1;
  });
  return bins;
}

function renderDistributionChart(values, dimension) {
  if (!values.length) {
    elements.distributionChart.innerHTML = '<div class="empty-state">当前筛选下没有原始值数据</div>';
    return;
  }

  const width = 980;
  const height = 550;
  const margin = { top: 24, right: 34, bottom: 46, left: 76 };
  const curveBottom = 344;
  const histogramTop = 398;
  const histogramBottom = height - margin.bottom;
  const maximum = Math.max(values[values.length - 1], 0.0001);
  const plotWidth = width - margin.left - margin.right;
  const xScale = (percent) => margin.left + (percent / 100) * plotWidth;
  const yScale = (value) => margin.top + (1 - value / maximum) * (curveBottom - margin.top);
  const yTicks = Array.from({ length: 5 }, (_, index) => (maximum * index) / 4);
  const xTicks = [0, 20, 40, 60, 80, 100];
  const binCount = 36;
  const bins = histogram(values, binCount, maximum);
  const maxBin = Math.max(...bins, 1);
  const barWidth = plotWidth / binCount;
  const thresholdLines = currentDistributionPercentiles().map((percent, index) => ({
    percent,
    value: percentile(values, percent),
    index,
  }));

  elements.distributionChart.innerHTML = `
    <svg viewBox="0 0 ${width} ${height}" role="img" aria-labelledby="distributionSvgTitle distributionSvgDesc">
      <title id="distributionSvgTitle">${escapeHtml(dimension.label)}原始值分布</title>
      <desc id="distributionSvgDesc">上方为按百分位排序的原始值曲线，下方为原始值直方图。</desc>
      ${yTicks
        .map(
          (value) => `
            <line class="distribution-grid" x1="${margin.left}" y1="${yScale(value)}" x2="${width - margin.right}" y2="${yScale(value)}" />
            <text class="distribution-axis-label" x="${margin.left - 12}" y="${yScale(value) + 4}" text-anchor="end">${rawValueText(value)}</text>`,
        )
        .join("")}
      ${xTicks
        .map(
          (percent) => `
            <line class="distribution-tick" x1="${xScale(percent)}" y1="${curveBottom}" x2="${xScale(percent)}" y2="${curveBottom + 5}" />
            <text class="distribution-axis-label" x="${xScale(percent)}" y="${curveBottom + 23}" text-anchor="middle">P${percent}</text>`,
        )
        .join("")}
      <line class="distribution-axis" x1="${margin.left}" y1="${margin.top}" x2="${margin.left}" y2="${curveBottom}" />
      <line class="distribution-axis" x1="${margin.left}" y1="${curveBottom}" x2="${width - margin.right}" y2="${curveBottom}" />
      <path class="distribution-curve" style="--dimension-color:${dimension.color}" d="${distributionCurvePath(values, xScale, yScale)}" />
      ${thresholdLines
        .map(
          ({ percent, value, index }) => `
            <line class="threshold-line threshold-${index + 1}" x1="${xScale(percent)}" y1="${margin.top}" x2="${xScale(percent)}" y2="${curveBottom}" />
            <circle class="threshold-point threshold-${index + 1}" cx="${xScale(percent)}" cy="${yScale(value)}" r="5" />
            <text class="threshold-label threshold-${index + 1}" x="${xScale(percent)}" y="${Math.max(margin.top + 13, yScale(value) - 10)}" text-anchor="middle">P${percentileText(percent)} · ${rawValueText(value)}</text>`,
        )
        .join("")}
      <text class="distribution-axis-title" transform="translate(18 ${margin.top + (curveBottom - margin.top) / 2}) rotate(-90)" text-anchor="middle">原始值</text>
      <text class="distribution-axis-title" x="${margin.left + plotWidth / 2}" y="${curveBottom + 42}" text-anchor="middle">全曲库百分位</text>
      ${bins
        .map((count, index) => {
          const barHeight = (count / maxBin) * (histogramBottom - histogramTop);
          return `<rect class="distribution-bar" style="--dimension-color:${dimension.color}" x="${margin.left + index * barWidth + 1}" y="${histogramBottom - barHeight}" width="${Math.max(1, barWidth - 2)}" height="${barHeight}" />`;
        })
        .join("")}
      <line class="distribution-axis" x1="${margin.left}" y1="${histogramBottom}" x2="${width - margin.right}" y2="${histogramBottom}" />
      <text class="distribution-axis-label" x="${margin.left}" y="${height - 10}" text-anchor="start">0</text>
      <text class="distribution-axis-label" x="${width - margin.right}" y="${height - 10}" text-anchor="end">${rawValueText(maximum)}</text>
      <text class="distribution-axis-title" x="${margin.left + plotWidth / 2}" y="${height - 10}" text-anchor="middle">原始值分布</text>
    </svg>`;
}

function renderRangeShare(values) {
  if (!values.length) {
    elements.rangeShare.innerHTML = "";
    return;
  }
  const thresholds = currentDistributionPercentiles().map((percent) => percentile(values, percent));
  const counts = Array.from({ length: thresholds.length + 1 }, () => 0);
  values.forEach((value) => {
    const index = thresholds.findIndex((threshold) => value <= threshold);
    counts[index === -1 ? thresholds.length : index] += 1;
  });
  const labels = [
    `≤ T1`,
    `T1–T2`,
    `T2–T3`,
    `T3–T4`,
    `> T4`,
  ];
  elements.rangeShare.innerHTML = counts
    .map((count, index) => {
      const share = (count / values.length) * 100;
      return `
        <span class="range-share-item range-${index + 1}" style="--range-width:${share}%">
          <i></i><small>${labels[index]}</small><strong>${count.toLocaleString("zh-CN")} · ${share.toFixed(1)}%</strong>
        </span>`;
    })
    .join("");
}

function renderDistributionResults() {
  const dimension = state.data.dimensions.find(
    (item) => item.key === state.distributionDimension,
  );
  if (!dimension) return;
  const entries = distributionEntries();
  const values = entries
    .map(({ chart }) => chart.rawScores[state.distributionDimension])
    .sort((left, right) => left - right);
  const total = values.reduce((sum, value) => sum + value, 0);
  applyDistributionMapping(values);
  const filterParts = [
    state.distributionKind === "all" ? "全部谱面" : state.distributionKind,
    difficultyText(state.distributionDifficulty),
    state.distributionLevel === "all" ? "全部等级" : state.distributionLevel,
  ];

  elements.distributionSummary.textContent = `${filterParts.join(" · ")} · ${values.length.toLocaleString("zh-CN")} 张谱面`;
  elements.distributionChartTitle.textContent = `${dimension.label} raw`;
  elements.distributionStats.innerHTML = [
    ["样本数", values.length.toLocaleString("zh-CN")],
    ["最小值", rawValueText(values[0] || 0)],
    ["中位数", rawValueText(percentile(values, 50))],
    ["平均值", rawValueText(values.length ? total / values.length : 0)],
    ["最大值", rawValueText(values[values.length - 1] || 0)],
  ]
    .map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`)
    .join("");
  elements.thresholdRows.innerHTML = currentDistributionPercentiles()
    .map((percent, index) => {
      const samplePosition = values.length
        ? Math.round(((values.length - 1) * percent) / 100) + 1
        : 0;
      return `<tr><td>T${index + 1} · P${percentileText(percent)}</td><td>${rawValueText(percentile(values, percent))}</td><td>${DISTRIBUTION_SCORE_ANCHORS[index]}</td><td>${samplePosition.toLocaleString("zh-CN")} / ${values.length.toLocaleString("zh-CN")}</td></tr>`;
    })
    .join("") + (
      values.length
        ? `<tr><td>T4_max</td><td>${rawValueText(values.at(-1))}</td><td>200</td><td>${values.length.toLocaleString("zh-CN")} / ${values.length.toLocaleString("zh-CN")}</td></tr>`
        : ""
    );
  syncThresholdControls(values);
  renderDistributionChart(values, dimension);
  renderRangeShare(values);
}

function renderDistribution() {
  renderDistributionDimensionTabs();
  renderDistributionResults();
}

function initials(title) {
  const trimmed = String(title || "?").trim();
  return trimmed.slice(0, 2).toUpperCase();
}

function coverMarkup(cover, title, className) {
  const placeholder = `<span class="cover-placeholder">${escapeHtml(initials(title))}</span>`;
  if (!cover) {
    return `<span class="${className}">${placeholder}</span>`;
  }
  return `
    <span class="${className}">
      <img src="${escapeHtml(cover)}" alt="${escapeHtml(title)} 曲绘" loading="lazy" decoding="async" />
      ${placeholder}
    </span>`;
}

function activateCoverImages(root = document) {
  root.querySelectorAll("img").forEach((image) => {
    image.addEventListener("load", () => {
      image.classList.add("is-loaded");
    });
    image.addEventListener("error", () => {
      image.classList.remove("is-loaded");
    });
    if (image.complete && image.naturalWidth > 0) {
      image.classList.add("is-loaded");
    }
  });
}

function chartMatches(chart, kind, difficulty) {
  return (
    (kind === "all" || chart.kind === kind) &&
    (difficulty === "all" || String(chart.difficulty) === String(difficulty))
  );
}

function levelQueryValues(query) {
  const tokens = normalized(query)
    .trim()
    .split(/[\s,，、/]+/)
    .filter(Boolean);
  if (!tokens.length || !tokens.every((token) => /^(?:[1-9]|1[0-5])\+?$/.test(token))) {
    return null;
  }
  return new Set(tokens);
}

function chartMatchesLibraryFilters(chart) {
  const queriedLevels = levelQueryValues(state.search);
  return (
    chartMatches(chart, state.kind, state.difficulty) &&
    (!queriedLevels || queriedLevels.has(normalized(chart.level)))
  );
}

function filteredSongs() {
  const query = normalized(state.search);
  const queriedLevels = levelQueryValues(query);
  return state.songs.filter((song) => {
    const textMatches =
      !query || queriedLevels || normalized(`${song.title} ${song.artist}`).includes(query);
    return (
      textMatches &&
      song.charts.some((chart) => chartMatchesLibraryFilters(chart))
    );
  });
}

function songTags(song) {
  const kindTags = song.chartKinds
    .map(
      (kind) =>
        `<span class="mini-tag kind-${kind}">${escapeHtml(kindText(kind))}</span>`,
    )
    .join("");
  const levelTags = [...new Set(song.charts.map((chart) => chart.level).filter(Boolean))]
    .map((level) => `<span class="mini-tag level-tag">${escapeHtml(level)}</span>`)
    .join("");
  return `${kindTags}${levelTags}`;
}

function renderSongList() {
  const songs = filteredSongs();
  elements.resultCount.textContent = songs.length.toLocaleString("zh-CN");

  if (!songs.length) {
    elements.songList.innerHTML = '<div class="empty-state">没有找到符合条件的歌曲</div>';
    return;
  }

  elements.songList.innerHTML = songs
    .map(
      (song) => `
        <button
          class="song-list-item ${song.id === state.selectedSongId ? "is-active" : ""}"
          type="button"
          data-song-id="${song.id}"
          aria-current="${song.id === state.selectedSongId ? "true" : "false"}"
        >
          ${coverMarkup(song.cover, song.title, "song-thumb")}
          <span class="song-list-copy">
            <span class="song-list-title">${escapeHtml(song.title)}</span>
            <span class="song-list-artist">${escapeHtml(song.artist)}</span>
            <span class="song-list-tags">${songTags(song)}</span>
          </span>
          <span class="song-chart-count">${song.charts.length}</span>
        </button>`,
    )
    .join("");

  elements.songList.querySelectorAll("[data-song-id]").forEach((button) => {
    button.addEventListener("click", () => selectSong(button.dataset.songId));
  });
  activateCoverImages(elements.songList);
}

function selectedSong() {
  return state.songs.find((song) => song.id === state.selectedSongId) || state.songs[0];
}

function selectedChart(song) {
  return song.charts.find((chart) => chart.id === state.selectedChartId) || song.charts[0];
}

function selectSong(songId, chartId = null, switchView = false) {
  const song = state.songs.find((item) => item.id === songId);
  if (!song) return;
  state.selectedSongId = song.id;
  const defaultChart = song.charts.find((chart) => chartMatchesLibraryFilters(chart));
  state.selectedChartId = chartId && song.charts.some((chart) => chart.id === chartId)
    ? chartId
    : (defaultChart || song.charts[0]).id;
  if (switchView) setView("library");
  renderSongList();
  renderDetail();
  updateHash();
  if (switchView) window.scrollTo({ top: 0, behavior: "smooth" });
}

function selectChart(chartId) {
  state.selectedChartId = chartId;
  renderDetail();
  updateHash();
}

function metadataItem(label, value) {
  return `<div><dt>${escapeHtml(label)}</dt><dd title="${escapeHtml(value)}">${escapeHtml(value)}</dd></div>`;
}

function renderDetail() {
  const song = selectedSong();
  if (!song) return;
  const chart = selectedChart(song);
  state.selectedSongId = song.id;
  state.selectedChartId = chart.id;
  const cover = chart.cover || song.cover;

  elements.detailCover.innerHTML = coverMarkup(cover, song.title, "detail-cover-inner").replace(
    'class="detail-cover-inner"',
    'class="detail-cover-inner" style="display:block;width:100%;height:100%"',
  );
  elements.songVersion.textContent = metadataText(chart.version);
  elements.songTitle.textContent = song.title;
  elements.songArtist.textContent = song.artist;
  elements.songFacts.innerHTML = `
    <span><strong>BPM</strong> ${escapeHtml(metadataText(chart.bpm))}</span>
    <span><strong>分类</strong> ${escapeHtml(metadataText(song.genre))}</span>
    <span><strong>时长</strong> ${durationText(chart.durationSeconds)}</span>
    <span><strong>物量</strong> ${Number.isFinite(chart.totalNotes) ? chart.totalNotes.toLocaleString("zh-CN") : "—"}</span>`;
  elements.chartCountLabel.textContent = `${song.charts.length} 个可用谱面`;

  elements.chartSwitcher.innerHTML = song.charts
    .map(
      (item) => `
        <button
          class="chart-option ${item.id === chart.id ? "is-active" : ""}"
          type="button"
          data-chart-id="${item.id}"
          aria-pressed="${item.id === chart.id ? "true" : "false"}"
        >
          <span class="kind-badge kind-${item.kind}">${escapeHtml(item.kindLabel)}</span>
          <span class="chart-option-copy">
            <strong>${escapeHtml(item.difficultyLabel)}</strong>
            <small>${escapeHtml(item.charter)}</small>
          </span>
          <span class="chart-level">${escapeHtml(item.level)}</span>
        </button>`,
    )
    .join("");
  elements.chartSwitcher.querySelectorAll("[data-chart-id]").forEach((button) => {
    button.addEventListener("click", () => selectChart(button.dataset.chartId));
  });

  const dominant = state.data.dimensions.find(
    (dimension) => dimension.key === chart.dominantDimension,
  );
  elements.chartMetadata.innerHTML = [
    metadataItem("谱面", metadataText(chart.kindLabel)),
    metadataItem("难度", `${metadataText(chart.difficultyLabel)} ${metadataText(chart.level)}`),
    metadataItem("谱师", metadataText(chart.charter)),
    metadataItem("BPM", metadataText(chart.bpm)),
    metadataItem("收录版本", metadataText(chart.version)),
    metadataItem("主导维度", dominant?.label || "—"),
  ].join("");

  renderRadar(chart);
  renderScoreBreakdown(chart);
  activateCoverImages(elements.detailCover);
}

function polarPoint(center, radius, index, count) {
  const angle = -Math.PI / 2 + (Math.PI * 2 * index) / count;
  return {
    x: center + Math.cos(angle) * radius,
    y: center + Math.sin(angle) * radius,
  };
}

function polygonPoints(points) {
  return points.map((point) => `${point.x.toFixed(2)},${point.y.toFixed(2)}`).join(" ");
}

function orderedRadarDimensions(dimensions) {
  if (dimensions.length !== 6) return dimensions;
  // Export order is top-left → top → top-right, then bottom-left → bottom → bottom-right.
  return [
    dimensions[1], dimensions[2], dimensions[5],
    dimensions[4], dimensions[3], dimensions[0],
  ];
}

function renderRadar(chart) {
  const dimensions = orderedRadarDimensions(state.data.dimensions);
  if (dimensions.length < 3) {
    elements.radarChart.innerHTML = `<div class="empty-state">当前为 ${dimensions.length} 个评分维度；至少 3 个维度时显示雷达图</div>`;
    return;
  }
  if (dimensions.some((dimension) => !Number.isFinite(chart.scores?.[dimension.key]))) {
    elements.radarChart.innerHTML = '<div class="empty-state">这张谱面的维度评分不完整</div>';
    return;
  }
  const size = 620;
  const center = size / 2;
  const frameRadius = 122;
  const scoreScale = Number(state.data.scoreScale) || 100;
  const displayMaximum = Math.max(scoreScale, Number(state.data.displayRange?.[1]) || 200);
  const maximumRadius = frameRadius * (displayMaximum / scoreScale);
  const rings = [0.25, 0.5, 0.75, 1].map((part) => scoreScale * part);
  const axes = dimensions.map((_, index) =>
    polarPoint(center, maximumRadius, index, dimensions.length),
  );
  const shape = dimensions.map((dimension, index) =>
    polarPoint(
      center,
      frameRadius * (
        Math.max(0, Math.min(displayMaximum, chart.scores[dimension.key])) / scoreScale
      ),
      index,
      dimensions.length,
    ),
  );
  const dominantDimension = dimensions.reduce((highest, dimension) =>
    chart.scores[dimension.key] > chart.scores[highest.key] ? dimension : highest,
  );
  const labels = dimensions.map((dimension, index) => {
    const point = polarPoint(center, maximumRadius + 32, index, dimensions.length);
    return `<text class="radar-label" style="--dimension-color:${dimension.color}" x="${point.x}" y="${point.y + 5}">${escapeHtml(
      dimension.shortLabel,
    )}</text>`;
  });

  elements.radarChart.innerHTML = `
    <svg style="--radar-color:${dominantDimension.color}" viewBox="0 0 ${size} ${size}" role="img" aria-labelledby="radarSvgTitle radarSvgDesc">
      <title id="radarSvgTitle">${escapeHtml(chart.difficultyLabel)} ${escapeHtml(
        chart.kindLabel,
      )} ${dimensions.length} 维雷达图</title>
      <desc id="radarSvgDesc">${scoreScale} 分为主框，超过主框的部分按真实比例显示；图形颜色取最高分维度</desc>
      ${rings
        .map((value) => {
          const points = dimensions.map((_, index) =>
            polarPoint(center, frameRadius * (value / scoreScale), index, dimensions.length),
          );
          return `<polygon class="radar-ring ${value === scoreScale ? "is-frame" : ""}" points="${polygonPoints(
            points,
          )}" />`;
        })
        .join("")}
      ${axes
        .map(
          (point) =>
            `<line class="radar-axis" x1="${center}" y1="${center}" x2="${point.x}" y2="${point.y}" />`,
        )
        .join("")}
      ${rings
        .map(
          (value) =>
            `<text class="radar-ring-label" x="${center + 5}" y="${center - frameRadius * (value / scoreScale) + 12}">${value}</text>`,
        )
        .join("")}
      <polygon class="radar-shape" points="${polygonPoints(shape)}" />
      ${shape
        .map(
          (point, index) =>
            `<circle class="radar-point" style="--dimension-color:${dimensions[index].color}" cx="${point.x}" cy="${point.y}" r="4.5" />`,
        )
        .join("")}
      ${labels.join("")}
    </svg>`;
}

function renderScoreBreakdown(chart) {
  const displayMaximum = Math.max(1, Number(state.data.displayRange?.[1]) || 200);
  elements.scoreBreakdown.innerHTML = state.data.dimensions
    .map((dimension) => {
      const score = chart.scores?.[dimension.key];
      const width = Number.isFinite(score)
        ? Math.max(0, Math.min(100, (score / displayMaximum) * 100))
        : 0;
      return `
        <div class="score-row" style="--dimension-color:${dimension.color};--score-width:${width}%">
          <span class="score-name"><span class="score-swatch"></span>${escapeHtml(
            dimension.label,
          )}</span>
          <span class="score-track"><span class="score-fill"></span></span>
          <span class="score-value">${scoreText(score)}</span>
        </div>`;
    })
    .join("");
}

function rankingRows() {
  return state.charts
    .filter((entry) =>
      chartMatches(entry.chart, state.rankingKind, state.rankingDifficulty) &&
      Number.isFinite(entry.chart.scores?.[state.rankingDimension]),
    )
    .sort((left, right) => {
      const scoreDifference =
        right.chart.scores[state.rankingDimension] - left.chart.scores[state.rankingDimension];
      if (scoreDifference) return scoreDifference;
      const rawDifference =
        (right.chart.rawScores?.[state.rankingDimension] ?? -Infinity) -
        (left.chart.rawScores?.[state.rankingDimension] ?? -Infinity);
      if (rawDifference) return rawDifference;
      return (right.chart.totalNotes || 0) - (left.chart.totalNotes || 0);
    });
}

function renderDimensionTabs() {
  elements.dimensionTabs.innerHTML = state.data.dimensions
    .map(
      (dimension) => `
        <button
          class="dimension-tab ${dimension.key === state.rankingDimension ? "is-active" : ""}"
          type="button"
          role="tab"
          aria-selected="${dimension.key === state.rankingDimension ? "true" : "false"}"
          data-dimension="${dimension.key}"
          style="--dimension-color:${dimension.color}"
        >${escapeHtml(dimension.label)}</button>`,
    )
    .join("");
  elements.dimensionTabs.querySelectorAll("[data-dimension]").forEach((button) => {
    button.addEventListener("click", () => {
      state.rankingDimension = button.dataset.dimension;
      state.visibleRanking = 100;
      renderRanking();
    });
  });
}

function renderRanking() {
  const dimension = state.data.dimensions.find(
    (item) => item.key === state.rankingDimension,
  );
  if (!dimension) return;
  const rows = rankingRows();
  const visible = rows.slice(0, state.visibleRanking);
  elements.rankingSummary.textContent = `${dimension.label} · ${rows.length.toLocaleString(
    "zh-CN",
  )} 张符合条件的谱面`;
  elements.loadMoreRanking.hidden = visible.length >= rows.length;
  renderDimensionTabs();

  if (!visible.length) {
    elements.rankingList.innerHTML = '<div class="empty-state">当前筛选下没有谱面</div>';
    return;
  }

  elements.rankingList.innerHTML = visible
    .map((entry, index) => {
      const score = entry.chart.scores[state.rankingDimension];
      return `
        <button
          class="ranking-row"
          type="button"
          data-ranking-song="${entry.song.id}"
          data-ranking-chart="${entry.chart.id}"
        >
          <span class="rank-number ${index < 3 ? "is-top" : ""}">${index + 1}</span>
          <span class="ranking-song">
            ${coverMarkup(entry.chart.cover || entry.song.cover, entry.song.title, "ranking-cover")}
            <span>
              <span class="ranking-title">${escapeHtml(entry.song.title)}</span>
              <span class="ranking-artist">${escapeHtml(entry.song.artist)}</span>
            </span>
          </span>
          <span class="ranking-charter-cell">
            <span class="ranking-charter">${escapeHtml(entry.chart.charter)}</span>
          </span>
          <span class="ranking-chart-cell">
            <span class="ranking-chart-meta">
              <span class="kind-badge kind-${entry.chart.kind}">${escapeHtml(
                entry.chart.kindLabel,
              )}</span>
              <span class="difficulty-badge">${escapeHtml(entry.chart.difficultyLabel)} ${escapeHtml(
                entry.chart.level,
              )}</span>
            </span>
          </span>
          <span class="ranking-score" style="--dimension-color:${dimension.color};--score-width:${Math.max(
            0,
            Math.min(100, (score / Math.max(1, Number(state.data.displayRange?.[1]) || 200)) * 100),
          )}%">
            <span class="score-track"><span class="score-fill"></span></span>
            <strong>${scoreText(score)}</strong>
          </span>
        </button>`;
    })
    .join("");

  elements.rankingList.querySelectorAll("[data-ranking-song]").forEach((button) => {
    button.addEventListener("click", () =>
      selectSong(button.dataset.rankingSong, button.dataset.rankingChart, true),
    );
  });
  activateCoverImages(elements.rankingList);
}

function setView(view) {
  state.view = view;
  document.body.dataset.view = view;
  const isLibrary = view === "library";
  const isRanking = view === "ranking";
  const isDistribution = view === "distribution";
  elements.librarySidebar.hidden = !isLibrary;
  elements.detailView.hidden = !isLibrary;
  elements.rankingView.hidden = !isRanking;
  elements.distributionView.hidden = !isDistribution;
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    const active = button.dataset.viewTarget === view;
    button.classList.toggle("is-active", active);
    button.setAttribute("aria-selected", String(active));
  });
  if (isRanking) renderRanking();
  if (isDistribution) renderDistribution();
  updateHash();
}

function updateHash() {
  const params = new URLSearchParams();
  params.set("view", state.view);
  if (state.selectedSongId) params.set("song", state.selectedSongId);
  if (state.selectedChartId) params.set("chart", state.selectedChartId);
  history.replaceState(null, "", `#${params.toString()}`);
}

function restoreHash() {
  const params = new URLSearchParams(location.hash.replace(/^#/, ""));
  const song = state.songs.find((item) => item.id === params.get("song"));
  if (song) {
    state.selectedSongId = song.id;
    state.selectedChartId = song.charts.some((chart) => chart.id === params.get("chart"))
      ? params.get("chart")
      : song.charts[0].id;
  }
  if (["library", "ranking", "distribution"].includes(params.get("view"))) {
    state.view = params.get("view");
  }
}

function bindEvents() {
  document.querySelectorAll("[data-view-target]").forEach((button) => {
    button.addEventListener("click", () => setView(button.dataset.viewTarget));
  });
  elements.searchInput.addEventListener("input", (event) => {
    state.search = event.target.value;
    state.visibleRanking = 100;
    renderSongList();
  });
  elements.kindFilter.addEventListener("change", (event) => {
    state.kind = event.target.value;
    state.visibleSongs = 100;
    renderSongList();
  });
  elements.difficultyFilter.addEventListener("change", (event) => {
    state.difficulty = event.target.value;
    state.visibleSongs = 100;
    renderSongList();
  });
  elements.rankingKindFilter.addEventListener("change", (event) => {
    state.rankingKind = event.target.value;
    state.visibleRanking = 100;
    renderRanking();
  });
  elements.rankingDifficultyFilter.addEventListener("change", (event) => {
    state.rankingDifficulty = event.target.value;
    state.visibleRanking = 100;
    renderRanking();
  });
  elements.loadMoreRanking.addEventListener("click", () => {
    state.visibleRanking += 100;
    renderRanking();
  });
  elements.distributionKindFilter.addEventListener("change", (event) => {
    state.distributionKind = event.target.value;
    renderDistributionResults();
  });
  elements.distributionDifficultyFilter.addEventListener("change", (event) => {
    state.distributionDifficulty = event.target.value;
    renderDistributionResults();
  });
  elements.distributionLevelFilter.addEventListener("change", (event) => {
    state.distributionLevel = event.target.value;
    renderDistributionResults();
  });
  elements.distributionDimensionTabs.addEventListener("click", (event) => {
    const button = event.target.closest("[data-distribution-dimension]");
    if (!button) return;
    state.distributionDimension = button.dataset.distributionDimension;
    renderDistribution();
  });
  elements.thresholdControls.addEventListener("input", (event) => {
    const input = event.target.closest("[data-threshold-index]");
    if (!input) return;
    setDistributionPercentile(Number(input.dataset.thresholdIndex), input.value);
  });
  elements.exportMappingProfile.addEventListener("click", exportMappingProfile);
  elements.resetDistributionThresholds.addEventListener("click", () => {
    state.distributionPercentilesByDimension[state.distributionDimension] = [
      ...DEFAULT_DISTRIBUTION_PERCENTILES,
    ];
    state.clientMappedDimensions.add(state.distributionDimension);
    renderDistributionResults();
  });
}

function populateDistributionLevels() {
  const levels = [...new Set(state.charts.map(({ chart }) => chart.level).filter(Boolean))]
    .sort((left, right) => {
      const numeric = (value) => Number.parseInt(value, 10) + (String(value).includes("+") ? 0.5 : 0);
      return numeric(left) - numeric(right) || left.localeCompare(right, "zh-CN");
    });
  elements.distributionLevelFilter.innerHTML = [
    '<option value="all">全部</option>',
    ...levels.map((level) => `<option value="${escapeHtml(level)}">${escapeHtml(level)}</option>`),
  ].join("");
}

function populateDifficultyFilters() {
  const difficulties = new Map();
  state.charts.forEach(({ chart }) => {
    if (chart.difficulty === null || chart.difficulty === undefined) return;
    const key = String(chart.difficulty);
    if (!difficulties.has(key)) {
      difficulties.set(key, chart.difficultyLabel || key);
    }
  });
  const numeric = (value) => Number.isFinite(Number(value)) ? Number(value) : Infinity;
  const options = [...difficulties.entries()]
    .sort(([left], [right]) => numeric(left) - numeric(right) || left.localeCompare(right))
    .map(([value, label]) => `<option value="${escapeHtml(value)}">${escapeHtml(label)}</option>`);
  [elements.difficultyFilter, elements.rankingDifficultyFilter, elements.distributionDifficultyFilter]
    .forEach((select) => {
      select.innerHTML = ['<option value="all">全部</option>', ...options].join("");
    });
}

async function init() {
  try {
    const response = await fetch("data/songs.json", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.data = await response.json();
    if (!Array.isArray(state.data.dimensions) || !state.data.dimensions.length) {
      throw new Error("评分维度为空");
    }
    if (!Array.isArray(state.data.songs)) throw new Error("歌曲数据格式无效");
    state.songs = state.data.songs;
    state.charts = state.songs.flatMap((song) =>
      song.charts.map((chart) => ({ song, chart })),
    );
    state.selectedSongId = state.songs[0]?.id || null;
    state.selectedChartId = state.songs[0]?.charts[0]?.id || null;
    state.rankingDimension = state.data.dimensions[0].key;
    state.distributionDimension = state.data.dimensions[0].key;
    state.distributionPercentilesByDimension = Object.fromEntries(
      state.data.dimensions.map((dimension) => [
        dimension.key,
        [...DEFAULT_DISTRIBUTION_PERCENTILES],
      ]),
    );
    elements.radarTitle.textContent = state.data.dimensions.length >= 3
      ? `${state.data.dimensions.length} 维图`
      : "评分详情";
    restoreHash();
    populateDifficultyFilters();
    populateDistributionLevels();
    renderThresholdControls();
    bindEvents();
    elements.songCount.textContent = state.data.stats.songCount.toLocaleString("zh-CN");
    elements.chartCount.textContent = state.data.stats.chartCount.toLocaleString("zh-CN");
    elements.appStatus.hidden = true;
    elements.appShell.hidden = false;
    renderSongList();
    renderDetail();
    setView(state.view);
  } catch (error) {
    elements.appStatus.classList.add("is-error");
    elements.appStatus.textContent = `数据载入失败：${error.message}。请先运行数据构建脚本，并通过 HTTP 静态服务器打开页面。`;
  }
}

init();

/* ResearchCompass project overview — fixed-corpus evaluation charts.
 * Values are the 2026-10-22 fixed-corpus runs summarized in
 * docs/project/2026-10-21-capability-gaps-and-dual-channel-plan.md;
 * the raw run artifacts live under the local, ignored test-results/ folder.
 */

const chartInstances = {};
const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;

function readToken(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

function chartPalette() {
  return {
    text: readToken('--chart-text') || '#b8d1d8',
    grid: readToken('--chart-grid') || 'rgba(160,216,228,.12)',
    teal: readToken('--chart-1') || '#65c9dc',
    amber: readToken('--chart-2') || '#f2b544',
    green: readToken('--chart-3') || '#5fd4a0',
    rose: readToken('--chart-4') || '#ff7a7a',
    surface: readToken('--surface-2') || '#0b202a',
  };
}

function percentTick(value) {
  return `${Math.round(value * 100)}%`;
}

function commonOptions(palette, { legend = false, max = 1 } = {}) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: reduceMotion ? false : { duration: 650, easing: 'easeOutQuart' },
    interaction: { mode: 'index', intersect: false },
    plugins: {
      legend: {
        display: legend,
        position: 'top',
        align: 'end',
        labels: {
          color: palette.text,
          usePointStyle: true,
          pointStyle: 'circle',
          boxWidth: 7,
          boxHeight: 7,
          padding: 14,
          font: { family: 'Inter, sans-serif', size: 11 },
        },
      },
      tooltip: {
        backgroundColor: '#041018',
        titleColor: '#eaf6f8',
        bodyColor: '#c5e2e8',
        borderColor: 'rgba(160,216,228,.25)',
        borderWidth: 1,
        padding: 10,
        displayColors: true,
        callbacks: {
          label: (context) => `${context.dataset.label || 'Score'}: ${(context.parsed.y * 100).toFixed(1)}%`,
        },
      },
    },
    scales: {
      x: {
        grid: { display: false },
        border: { display: false },
        ticks: { color: palette.text, font: { family: 'Inter, sans-serif', size: 10 } },
      },
      y: {
        beginAtZero: true,
        max,
        grid: { color: palette.grid },
        border: { display: false },
        ticks: {
          color: palette.text,
          stepSize: 0.25,
          callback: percentTick,
          font: { family: 'JetBrains Mono, monospace', size: 9 },
        },
      },
    },
  };
}

function baselineConfig(palette) {
  return {
    type: 'bar',
    data: {
      labels: ['Hit@1', 'Hit@3', 'Hit@5', 'MRR'],
      datasets: [{
        label: 'Score',
        data: [0.8, 0.8, 1.0, 0.845],
        backgroundColor: [palette.teal, palette.teal, palette.teal, palette.amber],
        borderColor: [palette.teal, palette.teal, palette.teal, palette.amber],
        borderWidth: 1,
        borderRadius: 7,
        borderSkipped: false,
        maxBarThickness: 64,
      }],
    },
    options: commonOptions(palette, { legend: false }),
  };
}

function ablationConfig(palette) {
  return {
    type: 'bar',
    data: {
      labels: ['Recall@10', 'Answer correctness', 'Answer coverage'],
      datasets: [
        {
          label: 'Reranker on',
          data: [0.3, 0.5, 1.0],
          backgroundColor: 'rgba(101,201,220,.78)',
          borderColor: palette.teal,
          borderWidth: 1,
          borderRadius: 6,
          borderSkipped: false,
          maxBarThickness: 42,
        },
        {
          label: 'Reranker off',
          data: [0.1, 0.3, 1.0],
          backgroundColor: 'rgba(242,181,68,.76)',
          borderColor: palette.amber,
          borderWidth: 1,
          borderRadius: 6,
          borderSkipped: false,
          maxBarThickness: 42,
        },
      ],
    },
    options: commonOptions(palette, { legend: true }),
  };
}

function synthesisConfig(palette) {
  const average = 0.8037;
  return {
    type: 'bar',
    data: {
      labels: ['q02', 'q03', 'q07', 'q09', 'q10'],
      datasets: [
        {
          label: 'Citation coverage',
          data: [0.7895, 0.875, 0.7778, 0.8261, 0.75],
          backgroundColor: 'rgba(101,201,220,.78)',
          borderColor: palette.teal,
          borderWidth: 1,
          borderRadius: 6,
          borderSkipped: false,
          maxBarThickness: 46,
        },
        {
          type: 'line',
          label: `Average ${average.toFixed(4)}`,
          data: [average, average, average, average, average],
          borderColor: palette.amber,
          backgroundColor: palette.amber,
          borderWidth: 1.5,
          borderDash: [5, 4],
          pointRadius: 0,
          pointHoverRadius: 3,
          fill: false,
        },
      ],
    },
    options: commonOptions(palette, { legend: true }),
  };
}

function applyChartTheme() {
  const palette = chartPalette();
  Chart.defaults.color = palette.text;
  Chart.defaults.borderColor = palette.grid;
  Chart.defaults.font.family = 'Inter, sans-serif';

  if (chartInstances.baseline) {
    const chart = chartInstances.baseline;
    chart.data.datasets[0].backgroundColor = [palette.teal, palette.teal, palette.teal, palette.amber];
    chart.data.datasets[0].borderColor = [palette.teal, palette.teal, palette.teal, palette.amber];
    chart.options.scales.x.ticks.color = palette.text;
    chart.options.scales.y.ticks.color = palette.text;
    chart.options.scales.y.grid.color = palette.grid;
    chart.update('none');
  }
  if (chartInstances.ablation) {
    const chart = chartInstances.ablation;
    chart.data.datasets[0].backgroundColor = 'rgba(101,201,220,.78)';
    chart.data.datasets[0].borderColor = palette.teal;
    chart.data.datasets[1].backgroundColor = 'rgba(242,181,68,.76)';
    chart.data.datasets[1].borderColor = palette.amber;
    chart.options.plugins.legend.labels.color = palette.text;
    chart.options.scales.x.ticks.color = palette.text;
    chart.options.scales.y.ticks.color = palette.text;
    chart.options.scales.y.grid.color = palette.grid;
    chart.update('none');
  }
  if (chartInstances.synthesis) {
    const chart = chartInstances.synthesis;
    chart.data.datasets[0].backgroundColor = 'rgba(101,201,220,.78)';
    chart.data.datasets[0].borderColor = palette.teal;
    chart.data.datasets[1].borderColor = palette.amber;
    chart.data.datasets[1].backgroundColor = palette.amber;
    chart.options.plugins.legend.labels.color = palette.text;
    chart.options.scales.x.ticks.color = palette.text;
    chart.options.scales.y.ticks.color = palette.text;
    chart.options.scales.y.grid.color = palette.grid;
    chart.update('none');
  }
}

function resizeCharts() {
  Object.values(chartInstances).forEach((chart) => chart.resize());
}

function initCharts() {
  if (!window.Chart) return;
  const palette = chartPalette();
  const baselineCanvas = document.getElementById('baselineChart');
  const ablationCanvas = document.getElementById('ablationChart');
  const synthesisCanvas = document.getElementById('synthesisChart');

  if (baselineCanvas) chartInstances.baseline = new Chart(baselineCanvas, baselineConfig(palette));
  if (ablationCanvas) chartInstances.ablation = new Chart(ablationCanvas, ablationConfig(palette));
  if (synthesisCanvas) chartInstances.synthesis = new Chart(synthesisCanvas, synthesisConfig(palette));

  applyChartTheme();
  document.addEventListener('themechange', applyChartTheme);
  document.addEventListener('tabchange', () => window.setTimeout(resizeCharts, 80));
  document.addEventListener('viewchange', () => window.setTimeout(resizeCharts, 80));
  window.addEventListener('resize', resizeCharts, { passive: true });
}

initCharts();

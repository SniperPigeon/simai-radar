using MajSimai;
using SimaiRadar.Analysis;
using SimaiRadar.Core;
using SimaiRadar.MajSimaiAdapter;
using SimaiRadar.Regression;

namespace SimaiRadar.Runtime;

public sealed class RadarComputationResult
{
    public RadarChartInput? ChartInput { get; set; }
    public RadarAnalysisResult? Analysis { get; set; }
    public double? FittedConstant { get; set; }
    public IReadOnlyList<string> Errors { get; set; } = Array.Empty<string>();
    public bool IsSuccess => ChartInput is not null && Analysis?.IsSuccess == true &&
                             FittedConstant is not null && Errors.Count == 0;
}

/// <summary>Unity-free public entry point for Play and standalone callers.</summary>
public sealed class RadarRuntime
{
    private readonly MajSimaiChartAdapter _adapter = new();
    private readonly RadarAnalyzer _analyzer = new();
    private readonly RegressionBetaModel _model = new();

    public RadarComputationResult Analyze(SimaiChart chart)
    {
        var adapted = _adapter.Adapt(chart);
        if (!adapted.IsSuccess)
            return new RadarComputationResult { Errors = adapted.Errors };
        return Analyze(adapted.Chart!);
    }

    public async Task<RadarComputationResult> ParseAndAnalyzeAsync(string inote)
    {
        var adapted = await _adapter.ParseAndAdaptAsync(inote).ConfigureAwait(false);
        if (!adapted.IsSuccess)
            return new RadarComputationResult { Errors = adapted.Errors };
        return Analyze(adapted.Chart!);
    }

    public RadarComputationResult Analyze(RadarChartInput chart)
    {
        try
        {
            var analysis = _analyzer.Analyze(chart);
            var result = new RadarComputationResult { ChartInput = chart, Analysis = analysis };
            if (!analysis.IsSuccess)
            {
                result.Errors = analysis.Features
                    .Where(pair => !pair.Value.IsSuccess)
                    .Select(pair => $"{pair.Key}: {pair.Value.Error}")
                    .ToArray();
                return result;
            }
            var raw = RadarFeatureNames.ModelInputOrder
                .Select(name => analysis.Features[name].Value!.Value).ToArray();
            result.FittedConstant = _model.Predict(raw);
            return result;
        }
        catch (Exception exception)
        {
            return new RadarComputationResult { ChartInput = chart, Errors = new[] { exception.Message } };
        }
    }
}

using SimaiRadar.Analysis.Features;
using SimaiRadar.Core;

namespace SimaiRadar.Analysis;

public sealed class RadarAnalyzer
{
    private readonly IReadOnlyList<(string Name, IRadarFeatureAnalyzer Analyzer)> _features =
        new (string, IRadarFeatureAnalyzer)[]
        {
            (RadarFeatureNames.Note, new NoteDensityAnalyzer()),
            (RadarFeatureNames.Peak, new PeakDensityAnalyzer()),
            (RadarFeatureNames.Sweep, new UnportedFeatureAnalyzer(RadarFeatureNames.Sweep)),
            (RadarFeatureNames.SlideTricky, new SlideTrickyAnalyzer()),
            (RadarFeatureNames.SlideSequence, new SlideSequenceAnalyzer()),
            (RadarFeatureNames.Jack, new JackSequenceAnalyzer()),
            (RadarFeatureNames.SlideCumulate, new SlideCumulateAnalyzer())
        };

    public RadarAnalysisResult Analyze(RadarChartInput? chart)
    {
        var results = new Dictionary<string, RadarFeatureResult>();
        if (chart is null)
        {
            foreach (var feature in _features)
                results[feature.Name] = RadarFeatureResult.Failure("Chart input is null.");
            return new RadarAnalysisResult { Features = results };
        }

        var context = new AnalysisContext(chart);
        foreach (var feature in _features)
        {
            try
            {
                var result = feature.Analyzer.Analyze(context);
                if (result.IsSuccess &&
                    (result.Value is null || double.IsNaN(result.Value.Value) ||
                     double.IsInfinity(result.Value.Value)))
                    throw new InvalidOperationException("Successful feature returned a non-finite value.");
                results[feature.Name] = result;
            }
            catch (Exception exception)
            {
                results[feature.Name] = RadarFeatureResult.Failure(exception.Message);
            }
        }
        return new RadarAnalysisResult { Features = results };
    }

    private sealed class UnportedFeatureAnalyzer : IRadarFeatureAnalyzer
    {
        private readonly string _name;
        internal UnportedFeatureAnalyzer(string name) => _name = name;
        public RadarFeatureResult Analyze(AnalysisContext context) =>
            RadarFeatureResult.Failure($"Feature '{_name}' has not been ported to C# yet.");
    }
}

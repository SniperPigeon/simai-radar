using SimaiRadar.Analysis;
using SimaiRadar.Analysis.Features;
using SimaiRadar.Core;
using SimaiRadar.MajSimaiAdapter;
using SimaiRadar.Regression;
using SimaiRadar.Runtime;
using Xunit;

namespace SimaiRadar.Runtime.Tests;

public sealed class AnalysisAndRegressionTests
{
    [Theory]
    [InlineData("(120){4}1,2,3,1,2,3,E", 2.0)]
    [InlineData("(120){4}1/2/3/4/5/6,,,,,,E", 2.6)]
    [InlineData("(120){4}1-5[4:1]*-5[4:1]*-5[4:1],E", 1.3333333333333333)]
    public async Task NoteMatchesReviewedPythonCases(string inote, double expected)
    {
        var analysis = await Analyze(inote);
        Assert.Equal(expected, analysis.Features[RadarFeatureNames.Note].Value!.Value, 12);
    }

    [Fact]
    public async Task PeakUsesFlatSlideAndHalfTouchGroups()
    {
        var adapted = await new MajSimaiChartAdapter().ParseAndAdaptAsync(
            "(120){4}1?<1<1[4:2]/A1/E1,E");
        Assert.True(adapted.IsSuccess, string.Join("; ", adapted.Errors));
        var analysis = new RadarAnalyzer().Analyze(adapted.Chart!);
        Assert.True(analysis.Features[RadarFeatureNames.Peak].IsSuccess);
        Assert.Equal(0.3, analysis.Features[RadarFeatureNames.Peak].Value!.Value, 12);
    }

    [Fact]
    public async Task JackAppliesExWeightsBeforeTopK()
    {
        var analysis = await Analyze("(180){16}1/1x,1xh[16:1],1bx,E");
        Assert.Equal(2.47, analysis.Features[RadarFeatureNames.Jack].Value!.Value, 12);
    }

    [Theory]
    [InlineData("(180){16}1,2,3,E", 1.5)]
    [InlineData("(180){16}2,3,2,3,E", 0.0)]
    [InlineData("(180){16}3,4,56,7,8,E", 3.2)]
    [InlineData("(180){16}73,84,15,26,37,48,51,26,37,E", 9.0)]
    [InlineData("(180){16}5,6,7,8,1,27,36,45,E", 5.5)]
    [InlineData("(180){16}1/2h[4:1],,3,4,E", 1.5)]
    [InlineData("(180){16}1,2,3,8,1,2,E", 3.6)]
    [InlineData("(180){16}1/1x,2x,3,E", 1.3)]
    public async Task SweepMatchesReviewedPythonCases(string inote, double expected)
    {
        var analysis = await Analyze(inote);
        Assert.Equal(expected, analysis.Features[RadarFeatureNames.Sweep].Value!.Value, 12);
    }

    [Theory]
    [InlineData("(180){16}1,28,37,46,5,46,37,28,1,E", "1,2,2,2,1,2,2,2,1")]
    [InlineData("(180){16}1,2,3,4,51,26,37,E", "1,1,1,1,2,2,2")]
    [InlineData("(180){16}18,27,36,45,6,7,8,E", "2,2,2,2,1,1,1")]
    public async Task SweepRecognitionPreservesVariableWidthMainSpine(
        string inote, string expectedWidths)
    {
        var adapted = await new MajSimaiChartAdapter().ParseAndAdaptAsync(inote);
        Assert.True(adapted.IsSuccess, string.Join("; ", adapted.Errors));

        var sequence = Assert.Single(SweepRecognizer.Recognize(adapted.Chart!.Events));

        Assert.Equal(expectedWidths,
            string.Join(",", sequence.Widths.Select(value => value.ToString())));
    }

    [Fact]
    public void SweepHandMotionMatchesReviewedDynamicProgrammingCases()
    {
        var adjacent = SweepHandMotion.Calculate(
            new[] { 0.0, 0.1, 0.2 },
            new IReadOnlyList<int>[] { new[] { 1 }, new[] { 2 }, new[] { 3 } });
        Assert.Equal((2, 2, 0, 0, 0), (
            adjacent.TotalDistance, adjacent.ActiveDistance, adjacent.IdleDistance,
            adjacent.FreeHandTakeovers, adjacent.FastJumpViolations));

        var takeover = SweepHandMotion.Calculate(
            new[] { 0.0, 0.1, 0.2 },
            new IReadOnlyList<int>[] { new[] { 1 }, new[] { 2 }, new[] { 6 } });
        Assert.Equal((1, 1, 0, 1, 0), (
            takeover.TotalDistance, takeover.ActiveDistance, takeover.IdleDistance,
            takeover.FreeHandTakeovers, takeover.FastJumpViolations));

        var doubleSweep = SweepHandMotion.Calculate(
            new[] { 0.0, 0.05 },
            new IReadOnlyList<int>[] { new[] { 1, 5 }, new[] { 3, 7 } });
        Assert.Equal(2, doubleSweep.FastJumpViolations);
        Assert.Equal(4, doubleSweep.TotalDistance);
    }

    [Fact]
    public async Task SweepLongRunAndPatternMotionMatchReviewedPythonCases()
    {
        var longBody = string.Join(",", Enumerable.Repeat("12345678", 3)
            .SelectMany(item => item.Select(character => character.ToString())));
        var longRun = await Analyze($"(180){{16}}{longBody},E");
        Assert.Equal(9.852385066637913,
            longRun.Features[RadarFeatureNames.Sweep].Value!.Value, 12);

        var patternBody = string.Join(",", Enumerable.Repeat(
            new[] { "1,2,3,4", "8,7,6,5" }, 4).SelectMany(item => item));
        var adapted = await new MajSimaiChartAdapter().ParseAndAdaptAsync(
            $"(180){{16}}{patternBody},E");
        Assert.True(adapted.IsSuccess, string.Join("; ", adapted.Errors));
        var result = SweepBurstAnalyzer.Score(
            adapted.Chart!.Events,
            Math.Max(adapted.Chart.ChartEndTimeSeconds,
                adapted.Chart.LastEventEndTimeSeconds ?? 0));
        Assert.Equal(12.75, result.Value, 12);
        Assert.Equal(12.0, result.BaseDensity, 12);
        Assert.Equal(0.75, result.MotionDensity, 12);
        Assert.Equal(7.5, result.RawMotionDensity, 12);
    }

    [Fact]
    public void SweepSelectionHandlesManyIndependentFamiliesWithoutRecursion()
    {
        var events = new List<RadarEvent>();
        for (var group = 0; group < 1_500; group++)
            for (var offset = 0; offset < 3; offset++)
            {
                var beat = new BeatPosition(group * 8 + offset, 4);
                events.Add(ButtonEvent(events.Count + 1, offset + 1, beat));
            }

        var sequences = SweepRecognizer.Recognize(events);

        Assert.Equal(1_500, sequences.Count);
    }

    [Fact]
    public void ExtremeContinuousSweepReturnsFeatureFailureBeforeUnboundedGrowth()
    {
        var events = Enumerable.Range(0, 5_000)
            .Select(index => ButtonEvent(
                index + 1, index % 8 + 1, new BeatPosition(index, 4)))
            .ToArray();
        var chart = new RadarChartInput
        {
            Events = events,
            ChartEndTimeSeconds = events[^1].StartTimeSeconds + 1,
            LastEventEndTimeSeconds = events[^1].EndTimeSeconds
        };

        var result = new RadarAnalyzer().Analyze(chart);

        Assert.Equal("partial", result.Status);
        Assert.True(result.Features[RadarFeatureNames.Note].IsSuccess);
        Assert.False(result.Features[RadarFeatureNames.Sweep].IsSuccess);
        Assert.Contains("budget exceeded", result.Features[RadarFeatureNames.Sweep].Error);
    }

    [Theory]
    [InlineData("(120){16}1-5[10:1],2,3,4,5,E", 3.5033834823231462)]
    [InlineData("(120){4}1-5[10:1],A1,E", 0.7500000000000001)]
    [InlineData("(120){4}1-5[10:1]/2-6[10:1]/3,,E", 0.5)]
    public async Task SlideCumulateMatchesReviewedPythonCases(string inote, double expected)
    {
        var analysis = await Analyze(inote);
        Assert.Equal(expected, analysis.Features[RadarFeatureNames.SlideCumulate].Value!.Value, 12);
    }

    [Theory]
    [InlineData("(120){8}1-5[10:1],2-6[10:1],E", 0.6783204105472322)]
    [InlineData("(120){4}1-5[10:1]/2-6[10:1],E", 0.16958010263680806)]
    [InlineData("(120){16}1-5[10:1],,2-6[10:1],3-7[10:1],,4-8[10:1],E", 0.5652670087893602)]
    public async Task SlideSequenceMatchesReviewedPythonCases(string inote, double expected)
    {
        var analysis = await Analyze(inote);
        Assert.Equal(expected, analysis.Features[RadarFeatureNames.SlideSequence].Value!.Value, 12);
    }

    [Theory]
    [InlineData("(120){16}1-5[10:1],2,3,4,5,E", 1.5262209237312725)]
    [InlineData("(120){4}1-5[10:1],A1,E", 0.25437015395521206)]
    [InlineData("(120){16}1-5[10:1],2,3,4,5,6,7,E", 1.9784345307627607)]
    [InlineData("(120){4}1-5[10:1]/2-6[10:1],3,E", 0.19501711803232927)]
    public async Task SlideTrickyMatchesReviewedPythonCases(string inote, double expected)
    {
        var analysis = await Analyze(inote);
        Assert.Equal(expected, analysis.Features[RadarFeatureNames.SlideTricky].Value!.Value, 10);
    }

    [Fact]
    public async Task PublicRuntimeReturnsAllSevenFeaturesAndFittedConstant()
    {
        var result = await new RadarRuntime().ParseAndAnalyzeAsync("(120){4}1,2,E");
        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        Assert.NotNull(result.Analysis);
        Assert.Equal("ok", result.Analysis!.Status);
        Assert.NotNull(result.FittedConstant);
        Assert.Empty(result.Errors);
    }

    [Fact]
    public async Task PublicRuntimeReturnsCancelledDataWithoutThrowing()
    {
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();

        var result = await new RadarRuntime().ParseAndAnalyzeAsync(
            "(120){4}1,2,E", cancellation.Token);

        Assert.False(result.IsSuccess);
        Assert.True(result.IsCancelled);
        Assert.Empty(result.Errors);
        Assert.Null(result.FittedConstant);
    }

    [Fact]
    public async Task UngroupedNoHeadSlideContributesToIntensityButNotSlideGroupFeatures()
    {
        var adapted = await new MajSimaiChartAdapter().ParseAndAdaptAsync(
            "(120){4}1?-5[4:1],E");
        Assert.True(adapted.IsSuccess, string.Join("; ", adapted.Errors));
        var slide = Assert.Single(adapted.Chart!.Events,
            item => item.Kind == RadarEventKind.Slide);
        Assert.Null(slide.SlideGroupId);

        var analysis = new RadarAnalyzer().Analyze(adapted.Chart);

        Assert.True(analysis.IsSuccess);
        Assert.True(analysis.Features[RadarFeatureNames.Note].Value > 0);
        Assert.Equal(0, analysis.Features[RadarFeatureNames.SlideTricky].Value);
        Assert.Equal(0, analysis.Features[RadarFeatureNames.SlideSequence].Value);
        Assert.Equal(0, analysis.Features[RadarFeatureNames.SlideCumulate].Value);
    }

    [Theory]
    [InlineData("(0){4}1,E", "non-finite or non-positive")]
    [InlineData("(120){4}1K5[4:1],E", "Extended K Slides")]
    public async Task AdaptationFailuresReturnDataWithoutLeakingExceptions(
        string inote,
        string expectedError)
    {
        RadarComputationResult? result = null;
        var exception = await Record.ExceptionAsync(async () =>
            result = await new RadarRuntime().ParseAndAnalyzeAsync(inote));

        Assert.Null(exception);
        Assert.NotNull(result);
        Assert.False(result!.IsSuccess);
        Assert.Null(result.ChartInput);
        Assert.Null(result.Analysis);
        Assert.Contains(expectedError, Assert.Single(result.Errors));
    }

    [Theory]
    [InlineData("(120){4}bad,E")]
    public async Task EmptyMajSimaiOutputIsUnavailableWithoutClaimingParseDiagnostics(string inote)
    {
        var result = await new RadarRuntime().ParseAndAnalyzeAsync(inote);

        Assert.False(result.IsSuccess);
        Assert.Null(result.ChartInput);
        Assert.Null(result.Analysis);
        Assert.Contains("no analyzable chart objects", Assert.Single(result.Errors));
        Assert.DoesNotContain(
            result.Errors,
            error => error.Contains("parse", StringComparison.OrdinalIgnoreCase));
    }

    [Fact]
    public void FeatureFailuresAreIsolatedInsideRadarAnalyzer()
    {
        var chart = new RadarChartInput
        {
            ChartEndTimeSeconds = 1,
            LastEventEndTimeSeconds = 1,
            Events = new[]
            {
                new RadarEvent
                {
                    EventId = 1,
                    Kind = RadarEventKind.Tap,
                    Position = "1",
                    StartBeat = BeatPosition.Zero,
                    EndBeat = BeatPosition.Zero
                },
                new RadarEvent
                {
                    EventId = 2,
                    Kind = RadarEventKind.Slide,
                    Position = "1",
                    SlideDeclareTimeSeconds = 0,
                    SlideDeclareBeat = BeatPosition.Zero,
                    StartTimeSeconds = 0.5,
                    EndTimeSeconds = 1,
                    StartBeat = new BeatPosition(1),
                    EndBeat = new BeatPosition(2),
                    SlideGroupId = 1,
                    SlidePath = Array.Empty<SlidePathSegment>()
                }
            }
        };

        var result = new RadarAnalyzer().Analyze(chart);

        Assert.Equal("partial", result.Status);
        Assert.True(result.Features[RadarFeatureNames.Jack].IsSuccess);
        Assert.False(result.Features[RadarFeatureNames.Note].IsSuccess);
        Assert.False(result.Features[RadarFeatureNames.SlideTricky].IsSuccess);
    }

    [Theory]
    [MemberData(nameof(RegressionVectors))]
    public void EmbeddedRegressionMatchesFrozenPythonVectors(double[] raw, double expected)
    {
        var actual = new RegressionBetaModel().Predict(raw);
        Assert.Equal(expected, actual, 9);
    }

    public static IEnumerable<object[]> RegressionVectors()
    {
        yield return Vector(new[] { 6.727708533441772, 10.213333333333333, 0.0, 3.500000000000045, 2.3435703486210016, 5.223136339994041, 0.32687803060913356 }, 13.18826234659077);
        yield return Vector(new[] { 5.3448652850017435, 9.373333333333333, 5.151860505357921, 3.8688372913206157, 1.527537949608422, 3.4463156768134153, 0.5098107142089721 }, 13.04867082557079);
        yield return Vector(new[] { 6.556341447489688, 9.728309501643864, 2.7754445459480404, 4.146342997793008, 1.977721867042476, 10.365354504785971, 0.276398788581657 }, 13.335359785629928);
        yield return Vector(new[] { 4.518910180239341, 7.165237190142859, 3.5146923810362276, 6.267413072276536, 1.4699399122083654, 2.9498185823014618, 0.5787667794900735 }, 13.074927456441534);
        yield return Vector(new[] { 8.675146946575055, 12.596563941258626, 14.182273623300125, 3.7269030666426044, 1.8412214862620846, 3.4609545033032254, 0.5401266930336726 }, 14.174345298608007);
        yield return Vector(new[] { 5.18055031687776, 7.697457300476198, 0.0, 4.252135533296645, 1.6280299529891435, 4.9236758330127754, 0.23376260698583867 }, 12.682036829185888);
        yield return Vector(new[] { 5.78473668281803, 8.333333333333334, 2.1322249739613124, 5.21319786300373, 1.4959570282923484, 27.349477481658905, 0.50129663471255 }, 13.52754748578909);
        yield return Vector(new[] { 5.43961412987999, 9.366666666666665, 0.0, 5.318180151048526, 2.5515716996273325, 3.307739064132819, 0.20708790434230476 }, 13.012286585333174);
    }

    private static object[] Vector(double[] raw, double expected) => new object[] { raw, expected };

    private static RadarEvent ButtonEvent(int eventId, int lane, BeatPosition beat)
    {
        var time = beat.ToDouble() / 3;
        return new RadarEvent
        {
            EventId = eventId,
            Kind = RadarEventKind.Tap,
            Position = lane.ToString(),
            StartBeat = beat,
            EndBeat = beat,
            StartTimeSeconds = time,
            EndTimeSeconds = time
        };
    }

    private static async Task<RadarAnalysisResult> Analyze(string inote)
    {
        var adapted = await new MajSimaiChartAdapter().ParseAndAdaptAsync(inote);
        Assert.True(adapted.IsSuccess, string.Join("; ", adapted.Errors));
        return new RadarAnalyzer().Analyze(adapted.Chart!);
    }
}

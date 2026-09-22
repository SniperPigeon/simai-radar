using SimaiRadar.Core;
using SimaiRadar.MajSimaiAdapter;
using Xunit;

namespace SimaiRadar.MajSimaiAdapter.Tests;

public sealed class MajSimaiChartAdapterTests
{
    [Fact]
    public void BeatBinningRemovesFloatingPointNoise()
    {
        var beat = BeatPosition.SnapFromDouble(
            0.33333333333333326,
            maxDenominator: 64,
            tolerance: 1e-12);

        Assert.Equal("1/3", beat.ToString());
    }

    [Fact]
    public void BeatBinningFallsBackToTheClosestAllowedSubdivision()
    {
        var beat = BeatPosition.SnapFromDouble(
            0.3141592653589793,
            maxDenominator: 16,
            tolerance: 0);

        Assert.Equal("5/16", beat.ToString());
    }

    [Fact]
    public async Task MapsTypedMajSimaiOutputWithoutReparsingTheChart()
    {
        var adapter = new MajSimaiChartAdapter();

        var result = await adapter.ParseAndAdaptAsync(
            "(120){4},1bx-5[4:1]b*-7[4:2]m,1?-3[4:1],(180)B1,,E");

        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        var chart = Assert.IsType<RadarChartInput>(result.Chart);
        Assert.Equal(2.1666666666666665, chart.ChartEndTimeSeconds, 12);
        Assert.Equal(2.0, chart.LastEventEndTimeSeconds);

        var slides = chart.Events.Where(item => item.Kind == RadarEventKind.Slide).ToArray();
        Assert.Equal(3, slides.Length);
        Assert.NotNull(slides[0].HeadEventId);
        Assert.Equal(slides[0].HeadEventId, slides[1].HeadEventId);
        Assert.Null(slides[2].HeadEventId);
        Assert.Equal(0.5, slides[0].SlideDeclareTimeSeconds);
        Assert.Equal("2", slides[0].StartBeat.ToString());
        Assert.Equal("3", slides[0].EndBeat.ToString());
        Assert.True(slides[0].IsBreak);
        Assert.True(slides[1].IsMine);

        var touch = Assert.Single(chart.Events, item => item.Kind == RadarEventKind.Touch);
        Assert.Equal("B1", touch.Position);
        Assert.Equal("3", touch.StartBeat.ToString());
        Assert.Equal(new[] { 120d, 180d }, chart.Events
            .Where(item => item.Kind == RadarEventKind.Timing)
            .Select(item => item.Bpm!.Value));
    }

    [Fact]
    public async Task ReconstructsFractionalBeatsFromActualCommaTimes()
    {
        var adapter = new MajSimaiChartAdapter();

        var result = await adapter.ParseAndAdaptAsync("(120){8}1,2,3h[8:1],,E");

        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        var objects = result.Chart!.Events.Where(item => item.Kind != RadarEventKind.Timing).ToArray();
        Assert.Equal(new[] { "0", "1/2", "1" }, objects.Select(item => item.StartBeat.ToString()));
        Assert.Equal("3/2", objects[2].EndBeat.ToString());
    }

    [Fact]
    public async Task BinsMajSimaiAccumulationAtAnUnfriendlyTempo()
    {
        var adapter = new MajSimaiChartAdapter();
        var slots = string.Join(",", Enumerable.Repeat(string.Empty, 48));

        var result = await adapter.ParseAndAdaptAsync($"(137){{192}}1,{slots}2,E");

        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        var taps = result.Chart!.Events.Where(item => item.Kind == RadarEventKind.Tap).ToArray();
        Assert.Equal("0", taps[0].StartBeat.ToString());
        Assert.Equal("1", taps[1].StartBeat.ToString());
    }

    [Fact]
    public async Task PreservesLeadingRestAndKeepsChartEndSeparateFromObjects()
    {
        var adapter = new MajSimaiChartAdapter();

        var result = await adapter.ParseAndAdaptAsync("(120){4},,1h[4:1],,,E");

        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        var hold = Assert.Single(result.Chart!.Events, item => item.Kind == RadarEventKind.Hold);
        Assert.Equal(1.0, hold.StartTimeSeconds);
        Assert.Equal("2", hold.StartBeat.ToString());
        Assert.True(result.Chart.ChartEndTimeSeconds > result.Chart.LastEventEndTimeSeconds);
    }

    [Fact]
    public async Task EmptyChartReturnsUnavailableData()
    {
        var result = await new MajSimaiChartAdapter().ParseAndAdaptAsync("(120){4},,E");

        Assert.False(result.IsSuccess);
        Assert.False(result.IsCancelled);
        Assert.Null(result.Chart);
        Assert.Contains("no analyzable chart objects", Assert.Single(result.Errors));
    }

    [Fact]
    public async Task CancellationReturnsDataWithoutParsing()
    {
        using var cancellation = new CancellationTokenSource();
        cancellation.Cancel();

        var result = await new MajSimaiChartAdapter().ParseAndAdaptAsync(
            "(120){4}1,E", cancellation.Token);

        Assert.False(result.IsSuccess);
        Assert.True(result.IsCancelled);
        Assert.Null(result.Chart);
        Assert.Empty(result.Errors);
    }

    [Fact]
    public async Task IntegratesAContinuedObjectAcrossBpmChangesAndPastChartEnd()
    {
        var adapter = new MajSimaiChartAdapter();

        var result = await adapter.ParseAndAdaptAsync("(120){4}1h[4:4],(240),,,,,E");

        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        var hold = Assert.Single(result.Chart!.Events, item => item.Kind == RadarEventKind.Hold);
        Assert.Equal(2.0, hold.EndTimeSeconds);
        Assert.Equal("7", hold.EndBeat.ToString());
        Assert.Equal(1.75, result.Chart.ChartEndTimeSeconds);
        Assert.Equal(2.0, result.Chart.LastEventEndTimeSeconds);
    }

    [Fact]
    public async Task RepeatedEqualBpmDoesNotShiftPhaseOrCreateAnalysisNoise()
    {
        var adapter = new MajSimaiChartAdapter();

        var result = await adapter.ParseAndAdaptAsync("(120){4}1,(120)2,E");

        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        var taps = result.Chart!.Events.Where(item => item.Kind == RadarEventKind.Tap).ToArray();
        Assert.Equal(new[] { "0", "1" }, taps.Select(item => item.StartBeat.ToString()));
        Assert.Single(result.Chart.Events, item => item.Kind == RadarEventKind.Timing);
    }

    [Fact]
    public async Task MapsEveryNonSlideNoteKindAndItsOwnFlags()
    {
        var adapter = new MajSimaiChartAdapter();

        var result = await adapter.ParseAndAdaptAsync("(120){4}A1h[4:1]/C/1b/2x/3m,E");

        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        var objects = result.Chart!.Events.Where(item => item.Kind != RadarEventKind.Timing).ToArray();
        Assert.Equal(
            new[]
            {
                RadarEventKind.TouchHold,
                RadarEventKind.Touch,
                RadarEventKind.Tap,
                RadarEventKind.Tap,
                RadarEventKind.Tap
            },
            objects.Select(item => item.Kind));
        Assert.Equal(new[] { "A1", "C", "1", "2", "3" }, objects.Select(item => item.Position));
        Assert.True(objects[2].IsBreak);
        Assert.True(objects[3].IsEx);
        Assert.True(objects[4].IsMine);
    }

    [Fact]
    public async Task CommentCommasDoNotAdvanceTheMajSimaiTimeline()
    {
        var adapter = new MajSimaiChartAdapter();

        var result = await adapter.ParseAndAdaptAsync("(120){4}1,|| comment,with,commas\n2,E");

        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        var taps = result.Chart!.Events.Where(item => item.Kind == RadarEventKind.Tap).ToArray();
        Assert.Equal(new[] { "0", "1" }, taps.Select(item => item.StartBeat.ToString()));
    }

    [Fact]
    public async Task KeepsDuplicateDeclarations()
    {
        var adapter = new MajSimaiChartAdapter();

        var result = await adapter.ParseAndAdaptAsync("(120){4}1/1,1-5[4:1]/1-5[4:1],E");

        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        Assert.Equal(2, result.Chart!.Events.Count(item =>
            item.Kind == RadarEventKind.Tap && item.StartTimeSeconds == 0));
        Assert.Equal(2, result.Chart.Events.Count(item => item.Kind == RadarEventKind.Slide));
    }

    [Fact]
    public async Task PreservesAmbiguousNoHeadSlidesWithoutGuessingTheirGroup()
    {
        var adapter = new MajSimaiChartAdapter();

        var result = await adapter.ParseAndAdaptAsync(
            "(120){4}1-5[4:1]*-7[4:1]/1?-3[4:1],E");

        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        var slides = result.Chart!.Events.Where(item => item.Kind == RadarEventKind.Slide).ToArray();
        Assert.Equal(3, slides.Length);
        Assert.Single(slides, item => item.SlideGroupId is not null);
        Assert.Equal(2, slides.Count(item => item.SlideGroupId is null));
    }

    [Fact]
    public async Task KeepsInterleavedIndependentNoHeadSlideGroupsSeparate()
    {
        var adapter = new MajSimaiChartAdapter();

        var result = await adapter.ParseAndAdaptAsync(
            "(120){4}4-2[4:1]/5-8[4:1]/4?-8[4:1]/5?-1[4:1],E");

        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        var slides = result.Chart!.Events.Where(item => item.Kind == RadarEventKind.Slide).ToArray();
        Assert.Equal(4, slides.Length);
        Assert.Equal(2, slides.Count(item => item.SlideGroupId is not null));
        Assert.Equal(2, slides.Count(item => item.SlideGroupId is null));
        Assert.Equal(2, slides.Count(item => item.HeadEventId is null));
    }

    [Fact]
    public async Task DistributesConnectedSlideTimeByFixedMajdataPlayBarCounts()
    {
        var adapter = new MajSimaiChartAdapter();

        var result = await adapter.ParseAndAdaptAsync("(120){4}1-3-5[4:2],E");

        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        var slide = Assert.Single(result.Chart!.Events, item => item.Kind == RadarEventKind.Slide);
        Assert.Equal(2, slide.SlidePath!.Count);
        Assert.Equal(slide.StartTimeSeconds, slide.SlidePath[0].StartTimeSeconds);
        Assert.Equal(slide.EndTimeSeconds, slide.SlidePath[1].EndTimeSeconds);
        Assert.Equal(1.0, slide.SlidePath[0].EndTimeSeconds, 12);
        Assert.Equal(slide.SlidePath[0].EndTimeSeconds, slide.SlidePath[1].StartTimeSeconds);
    }

    [Theory]
    [InlineData("1-3[4:1]", "-", 1, null, 3)]
    [InlineData("1^3[4:1]", "^", 1, null, 3)]
    [InlineData("1v3[4:1]", "v", 1, null, 3)]
    [InlineData("1>3[4:1]", ">", 1, null, 3)]
    [InlineData("1<7[4:1]", "<", 1, null, 7)]
    [InlineData("1p3[4:1]", "p", 1, null, 3)]
    [InlineData("1q7[4:1]", "q", 1, null, 7)]
    [InlineData("1V75[4:1]", "V", 1, 7, 5)]
    [InlineData("1pp5[4:1]", "pp", 1, null, 5)]
    [InlineData("1qq5[4:1]", "qq", 1, null, 5)]
    [InlineData("1s5[4:1]", "s", 1, null, 5)]
    [InlineData("1z5[4:1]", "z", 1, null, 5)]
    [InlineData("1w5[4:1]", "w", 1, null, 5)]
    public void InterpretsOnlyTheSlidePathRetainedByMajSimai(
        string rawContent,
        string shape,
        int start,
        int? via,
        int end)
    {
        var resolver = new SlidePathResolver();

        var path = Assert.Single(resolver.Resolve(rawContent, 1.0, 2.0));

        Assert.Equal(shape, path.Shape);
        Assert.Equal(start, path.StartPosition);
        Assert.Equal(via, path.ViaPosition);
        Assert.Equal(end, path.EndPosition);
        Assert.True(path.BarCount > 0);
    }

    [Fact]
    public async Task ExtendedSlideReturnsFailureWithoutThrowingAcrossThePublicBoundary()
    {
        var adapter = new MajSimaiChartAdapter();

        var result = await adapter.ParseAndAdaptAsync("(120){4}1K5[4:1],E");

        Assert.False(result.IsSuccess);
        Assert.Null(result.Chart);
        Assert.NotEmpty(result.Errors);
    }

    [Fact]
    public async Task ExtendedSlideUsesCallerSuppliedPlayGeometry()
    {
        var provider = new FixedExtendedSlideProvider(22);
        var adapter = new MajSimaiChartAdapter(provider);

        var result = await adapter.ParseAndAdaptAsync("(120){4}1P6K7[4:1],E");

        Assert.True(result.IsSuccess, string.Join("; ", result.Errors));
        Assert.Equal("1P6K7[4:1]", provider.RawContent);
        var slide = Assert.Single(result.Chart!.Events, item => item.Kind == RadarEventKind.Slide);
        var path = Assert.Single(slide.SlidePath!);
        Assert.Equal("slidecode", path.Shape);
        Assert.Equal(1, path.StartPosition);
        Assert.Equal(7, path.EndPosition);
        Assert.Equal(22, path.BarCount);
        Assert.Equal(slide.StartTimeSeconds, path.StartTimeSeconds);
        Assert.Equal(slide.EndTimeSeconds, path.EndTimeSeconds);
    }

    private sealed class FixedExtendedSlideProvider : IExtendedSlideBarCountProvider
    {
        private readonly int _barCount;
        internal FixedExtendedSlideProvider(int barCount) => _barCount = barCount;
        internal string? RawContent { get; private set; }

        public int ResolveBarCount(string rawContent)
        {
            RawContent = rawContent;
            return _barCount;
        }
    }
}

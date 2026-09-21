using System.Text.Json;
using SimaiRadar.Analysis.Features;
using SimaiRadar.Runtime;

if (args.Length is < 1 or > 2 || args.Length == 2 && args[1] != "--sweep-events")
{
    Console.Error.WriteLine(
        "Usage: dotnet run --project integrations/csharp-runtime-probe -- inote.txt [--sweep-events]");
    return 2;
}

var inote = await File.ReadAllTextAsync(args[0]);
var result = await new RadarRuntime().ParseAndAnalyzeAsync(inote);
var includeSweepEvents = args.Length == 2;
var output = new
{
    result.IsSuccess,
    result.FittedConstant,
    result.Errors,
    chart = result.ChartInput is null ? null : new
    {
        eventCount = result.ChartInput.Events.Count,
        result.ChartInput.ChartEndTimeSeconds,
        result.ChartInput.LastEventEndTimeSeconds,
        sweepEvents = !includeSweepEvents ? null : result.ChartInput.Events
            .Where(item => item.Kind is SimaiRadar.Core.RadarEventKind.Tap or
                SimaiRadar.Core.RadarEventKind.Hold)
            .Select(item => new
            {
                item.EventId,
                kind = item.Kind == SimaiRadar.Core.RadarEventKind.Tap ? "tap" : "hold",
                item.IsSlideHead,
                item.StartTimeSeconds,
                item.EndTimeSeconds,
                startBeat = item.StartBeat.ToString(),
                endBeat = item.EndBeat.ToString(),
                item.Position,
                item.IsBreak,
                item.IsEx,
                item.IsMine
            }).ToArray(),
        correctedWorkload = Workload.CorrectedPoints(result.ChartInput.Events)
            .Select(item => new { item.TimeSeconds, item.Weight }).ToArray(),
        slideGroups = result.ChartInput.Events
            .Where(item => item.Kind == SimaiRadar.Core.RadarEventKind.Slide)
            .GroupBy(item => item.SlideGroupId)
            .Select(group => new
            {
                groupId = group.Key,
                position = group.First().Position,
                declarationTime = group.First().SlideDeclareTimeSeconds,
                pathCount = group.Count(),
                totalBars = group.Sum(item => item.SlidePath?.Sum(segment => segment.BarCount) ?? 0),
                starts = group.Select(item => item.StartTimeSeconds).OrderBy(value => value).ToArray()
            }).ToArray()
    },
    status = result.Analysis?.Status,
    features = result.Analysis?.Features.ToDictionary(
        item => item.Key,
        item => new
        {
            item.Value.IsSuccess,
            item.Value.Value,
            item.Value.Error
        })
};
Console.WriteLine(JsonSerializer.Serialize(output, new JsonSerializerOptions { WriteIndented = true }));
return 0;

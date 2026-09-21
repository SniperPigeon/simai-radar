using System.Text.Json;
using SimaiRadar.Analysis.Features;
using SimaiRadar.Runtime;

if (args.Length != 1)
{
    Console.Error.WriteLine("Usage: dotnet run --project integrations/csharp-runtime-probe -- inote.txt");
    return 2;
}

var inote = await File.ReadAllTextAsync(args[0]);
var result = await new RadarRuntime().ParseAndAnalyzeAsync(inote);
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

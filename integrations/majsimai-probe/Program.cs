using System.Collections;
using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using MajSimai;

// This is a disposable format probe, not the Play runtime adapter. Keep its
// package version explicit so a probe result always identifies its parser.
var paths = args.Where(arg => arg != "--schema").ToArray();
if (args.Any(arg => arg is "-h" or "--help") || paths.Length > 1)
{
    Console.Error.WriteLine("Usage: dotnet run --project integrations/majsimai-probe -- [--schema] [fumen.txt]");
    return paths.Length > 1 ? 2 : 0;
}

var showSchema = args.Contains("--schema");
var path = paths.FirstOrDefault();
var fumen = path is null
    ? "(120){4}1,1-5[4:1],B1h[4:1],,E"
    : await File.ReadAllTextAsync(path);

try
{
    var chart = await SimaiParser.ParseChartAsync(fumen);
    var output = new
    {
        package = "Lingfeng-bbben.MajSimai",
        version = typeof(SimaiParser).Assembly.GetName().Version?.ToString(),
        input = fumen,
        chart = new
        {
            chart.Level,
            chart.Designer,
            chart.Fumen,
            noteTimings = chart.NoteTimings.ToArray()
                .Select(point => Project(point, new HashSet<object>(ReferenceEqualityComparer.Instance), 0)),
            commaTimings = chart.CommaTimings.ToArray()
                .Select(point => Project(point, new HashSet<object>(ReferenceEqualityComparer.Instance), 0))
        },
        types = !showSchema ? null : typeof(SimaiParser).Assembly.GetExportedTypes()
            .Where(t => t.Namespace == "MajSimai" &&
                (t.Name.StartsWith("SimaiChart") || t.Name.StartsWith("SimaiTiming") ||
                 t.Name.StartsWith("SimaiNote") || t.Name.StartsWith("SimaiSlide")))
            .OrderBy(t => t.Name)
            .Select(t => new
            {
                name = t.FullName,
                members = t.GetMembers(BindingFlags.Public | BindingFlags.Instance)
                    .Where(m => m is FieldInfo or PropertyInfo)
                    .OrderBy(m => m.Name)
                    .Select(m => new
                    {
                        name = m.Name,
                        type = m is FieldInfo field ? field.FieldType.ToString() :
                            ((PropertyInfo)m).PropertyType.ToString()
                    })
            })
    };
    Console.WriteLine(JsonSerializer.Serialize(output, new JsonSerializerOptions
    {
        WriteIndented = true,
        DefaultIgnoreCondition = JsonIgnoreCondition.WhenWritingNull
    }));
    return 0;
}
catch (Exception exception)
{
    Console.Error.WriteLine(exception);
    return 1;
}

static object? Project(object? value, HashSet<object> seen, int depth)
{
    if (value is null) return null;
    if (value.GetType().IsEnum) return value.ToString();
    if (value is string or bool or char || value.GetType().IsPrimitive ||
        value is decimal)
        return value;
    if (depth >= 8) return $"<depth-limit:{value.GetType().Name}>";

    var type = value.GetType();
    if (!type.IsValueType && !seen.Add(value)) return $"<cycle:{type.Name}>";
    try
    {
        if (value is IEnumerable sequence)
            return sequence.Cast<object?>().Take(1000)
                .Select(item => Project(item, seen, depth + 1)).ToArray();

        var members = type.GetMembers(BindingFlags.Public | BindingFlags.Instance)
            .Where(member => member is FieldInfo or PropertyInfo)
            .OrderBy(member => member.Name);
        var result = new Dictionary<string, object?> { ["$type"] = type.FullName };
        foreach (var member in members)
        {
            if (member is PropertyInfo property && property.GetIndexParameters().Length != 0)
                continue;
            try
            {
                var memberValue = member is FieldInfo field ? field.GetValue(value) :
                    ((PropertyInfo)member).GetValue(value);
                result[member.Name] = Project(memberValue, seen, depth + 1);
            }
            catch (Exception exception)
            {
                result[member.Name] = $"<getter-error:{exception.GetType().Name}>";
            }
        }
        return result;
    }
    finally
    {
        if (!type.IsValueType) seen.Remove(value);
    }
}

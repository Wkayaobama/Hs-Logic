function escapeCsvField(value: string | number): string {
  let str = String(value);
  // CRM-sourced text is attacker-controllable (form submissions); a leading
  // =, +, -, @, tab or CR executes as a formula when opened in Excel (OWASP
  // CSV injection), so neutralize it with a leading apostrophe.
  if (/^[=+\-@\t\r]/.test(str)) {
    str = `'${str}`;
  }
  if (/[",\r\n]/.test(str)) {
    return `"${str.replace(/"/g, '""')}"`;
  }
  return str;
}

export function downloadCsv(
  filename: string,
  rows: Array<Record<string, string | number>>
): void {
  if (rows.length === 0) return;

  const headers = Object.keys(rows[0]);
  const lines = [
    headers.map(escapeCsvField).join(","),
    ...rows.map((row) =>
      headers.map((key) => escapeCsvField(row[key] ?? "")).join(",")
    ),
  ];

  const csvContent = lines.join("\n");
  const blob = new Blob([csvContent], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);

  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.appendChild(link);
  link.click();
  document.body.removeChild(link);
  URL.revokeObjectURL(url);
}

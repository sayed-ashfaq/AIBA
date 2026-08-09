// Formatting a result value for display in the table beside a chart.

const fullNumber = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

export const formatFull = (value) => (typeof value === "number" ? fullNumber.format(value) : String(value ?? "—"));

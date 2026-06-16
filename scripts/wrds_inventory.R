# wrds_inventory.R
# One-off: determine which WRDS libraries (Postgres schemas) this account can
# actually SELECT from = the institution's real subscription. Ground-truth for
# RAiner's data-availability list (rainer/data/*.json).
#
# NOTE: pg_namespace lists the *entire* WRDS catalog (~1000+ schemas), not just
# subscribed ones. Access is granted via group roles, so we test
# has_schema_privilege(nspname, 'USAGE') per schema to get the real subscription.
#
# Credentials (NEVER commit real ones) — provided in ONE of these ways, in order:
#   1. env vars WRDS_USERNAME + WRDS_PASSWORD
#   2. a CSV at $WRDS_SECRETS (cols: service,username,password; row service==wrds)
#   3. a gitignored ./secrets.csv in the repo root (same format)
# See scripts/secrets.csv.example for the format.
#
# Usage:
#   WRDS_USERNAME=you WRDS_PASSWORD=*** Rscript scripts/wrds_inventory.R
#   # or, with a gitignored ./secrets.csv present:  Rscript scripts/wrds_inventory.R
# Outputs:
#   - prints accessible-vs-total counts and vendor-pattern matches
#   - writes /tmp/wrds_access.csv (schema, can_select)

suppressMessages({
  library(DBI)
  library(RPostgres)
  library(readr)
  library(dplyr)
})

# --- Credentials: env vars first, then $WRDS_SECRETS, then ./secrets.csv -------
get_wrds_credentials <- function() {
  env_user <- Sys.getenv("WRDS_USERNAME")
  env_pass <- Sys.getenv("WRDS_PASSWORD")
  if (nzchar(env_user) && nzchar(env_pass)) {
    return(list(username = env_user, password = env_pass))
  }
  path <- path.expand(Sys.getenv("WRDS_SECRETS", unset = "secrets.csv"))
  if (!file.exists(path)) {
    stop("No WRDS credentials. Set WRDS_USERNAME + WRDS_PASSWORD, or create ",
         path, " (see scripts/secrets.csv.example).")
  }
  secrets <- readr::read_csv(path, show_col_types = FALSE)
  if (!all(c("service", "username", "password") %in% names(secrets))) {
    stop(path, " must have columns: service,username,password")
  }
  row <- dplyr::filter(secrets, service == "wrds")
  if (nrow(row) != 1) stop(path, " must contain exactly one row with service == 'wrds'")
  list(username = row$username[[1]], password = row$password[[1]])
}

creds <- get_wrds_credentials()
message("Connecting to WRDS as user: ", creds$username)
wrds <- DBI::dbConnect(
  RPostgres::Postgres(),
  host = "wrds-pgdata.wharton.upenn.edu", port = 9737,
  user = creds$username, password = creds$password,
  sslmode = "require", gssencmode = "disable", dbname = "wrds", bigint = "numeric"
)
on.exit(try(DBI::dbDisconnect(wrds), silent = TRUE), add = TRUE)
if (!DBI::dbIsValid(wrds)) stop("Failed to connect to WRDS")
message("Connected OK")

# USAGE privilege per schema = the institution's real subscription. Uses
# has_schema_privilege (returns FALSE instead of erroring on no-access schemas).
acc <- DBI::dbGetQuery(wrds, "
  SELECT nspname AS schema,
         has_schema_privilege(nspname, 'USAGE') AS can_select
  FROM pg_namespace
  WHERE nspname NOT LIKE 'pg_%'
    AND nspname NOT IN ('information_schema')
  ORDER BY nspname
")
readr::write_csv(acc, "/tmp/wrds_access.csv")
accessible <- acc |> dplyr::filter(can_select)

cat(sprintf("\n===== WRDS ACCESS: %d of %d schemas accessible (USAGE) =====\n",
            nrow(accessible), nrow(acc)))
cat("[full table saved to /tmp/wrds_access.csv]\n")

cat("\n===== ACCESSIBLE SCHEMAS (real subscription) =====\n")
cat(paste(accessible$schema, collapse = ", "), "\n")

# Vendor probes for the previously over-flagged sources -----------------------
vendor_patterns <- c(
  "Audit Analytics"      = "audit",
  "Capital IQ / S&P CIQ" = "ciq",
  "S&P transcripts"      = "transcript",
  "NielsenIQ"            = "nielsen|niq|kilts",
  "Circana / IRI"        = "circana|^iri",
  "GfK"                  = "gfk",
  "ExecuComp"            = "execcomp|execucomp",
  "Compustat"            = "^comp",
  "CRSP"                 = "^crsp",
  "IBES"                 = "ibes",
  "OptionMetrics"        = "optionm",
  "BoardEx"              = "boardex",
  "ISS / RiskMetrics"    = "^iss|riskmetrics",
  "Mergent FISD"         = "fisd|mergent",
  "TAQ"                  = "taq",
  "RavenPack"            = "ravenpack",
  "Markit"               = "markit",
  "Thomson 13F (s34)"    = "tr_|^s34|thomson"
)
cat("\n===== VENDOR PROBES (accessible schemas matching each vendor) =====\n")
for (lbl in names(vendor_patterns)) {
  hit_all <- acc$schema[grepl(vendor_patterns[[lbl]], acc$schema, ignore.case = TRUE)]
  hit_ok  <- accessible$schema[grepl(vendor_patterns[[lbl]], accessible$schema, ignore.case = TRUE)]
  verdict <- if (length(hit_ok)) "ACCESSIBLE" else if (length(hit_all)) "in catalog, NO access" else "not in catalog"
  cat(sprintf("%-22s : %-22s %s\n", lbl, verdict,
              if (length(hit_ok)) paste(utils::head(hit_ok, 8), collapse = ", ") else ""))
}
cat("\nDone.\n")

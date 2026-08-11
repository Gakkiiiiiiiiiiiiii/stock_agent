{{- define "stock-agent.name" -}}
{{- .Chart.Name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- define "stock-agent.fullname" -}}
{{- printf "%s-%s" .Release.Name (include "stock-agent.name" .) | trunc 63 | trimSuffix "-" }}
{{- end }}

#Requires -Version 7.0
<#
.SYNOPSIS  Predefined queries of the sampling probe.
.NOTES     Preflight: one row per object through the backend (proves executability).
           References: model/reference.json (known answers captured 2026-09-14), evaluated
           by stage 07 through the backend count endpoint, the flattened extract and,
           optionally, the HubSpot REST API directly.
#>

$Queries = [ordered]@{
    Preflight = @(
        [ordered]@{ Object = 'deals';     Entity = 'SEALSQ'; Properties = 'dealname,pipeline,prod___product_name_new_,wisekey___seal'; ExpectProperties = @('dealname', 'pipeline', 'hs_object_id') }
        [ordered]@{ Object = 'contacts';  Entity = 'ICALPS'; Properties = 'email,firstname,lastname,icalps_contactstatus';           ExpectProperties = @('email', 'hs_object_id') }
        [ordered]@{ Object = 'companies'; Entity = 'ICALPS'; Properties = 'name,domain,icalps_companytype';                          ExpectProperties = @('name', 'domain', 'hs_object_id') }
        [ordered]@{ Object = 'tickets';   Entity = 'ICALPS'; Properties = 'subject,is_icalps,hs_pipeline';                            ExpectProperties = @('subject', 'hs_object_id') }
        [ordered]@{ Object = 'notes';     Entity = $null;    Properties = 'hs_timestamp';                                             ExpectProperties = @('hs_timestamp', 'hs_object_id') }
    )
}

function ConvertTo-CountFilterParams {
    <# reference.json filters -> backend `filter=prop:OP:value` strings #>
    param([object[]]$Filters)
    $out = @()
    foreach ($f in @($Filters)) {
        $prop = Get-RowValue $f 'prop'; $op = Get-RowValue $f 'op'; $value = Get-RowValue $f 'value'
        if ($op -in @('HAS_PROPERTY', 'NOT_HAS_PROPERTY')) { $out += "${prop}:${op}" }
        else { $out += "${prop}:${op}:${value}" }
    }
    return $out
}

function ConvertTo-HubSpotFilters {
    <# reference.json filters -> HubSpot search API filter objects #>
    param([object[]]$Filters)
    $out = @()
    foreach ($f in @($Filters)) {
        $prop = Get-RowValue $f 'prop'; $op = Get-RowValue $f 'op'; $value = [string](Get-RowValue $f 'value')
        $filter = [ordered]@{ propertyName = $prop; operator = $op }
        if ($op -in @('IN', 'NOT_IN')) { $filter.values = @($value -split '\|' | Where-Object { $_ }) }
        elseif ($op -notin @('HAS_PROPERTY', 'NOT_HAS_PROPERTY')) { $filter.value = $value }
        $out += $filter
    }
    return $out
}

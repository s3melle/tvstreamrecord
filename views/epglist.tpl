%include('create.tpl')
<div id="keyword_for_epg" keyword_for_epg="{{keyword_for_epg}}"></div>
<div id="delta_before_epg" delta="{{deltab}}"></div>
<div id="delta_after_epg" delta="{{deltaa}}"></div>
    <div id="listmode" value="{{listmode}}"></div>
<div id="users-contain" class="ui-widget">
    <div id="epg_list_filters" style="margin:8px 0; display:flex; flex-wrap:wrap; align-items:center; gap:8px;">
        <label class="epg-filter-toggle" for="epg_time_filter_enabled">
            <input type="checkbox" id="epg_time_filter_enabled" checked="checked" />
            Filtrer par date et plage horaire
        </label>
        <span id="epg_custom_range" style="display:inline-flex; align-items:center; gap:5px;">
            <label for="epg_filter_date">Du :</label>
            <input type="text" id="epg_filter_date" style="width:105px;" />
            <label for="epg_filter_from">à :</label>
            <input type="text" id="epg_filter_from" value="00:00" style="width:65px;" />
            <label for="epg_filter_date_end">au :</label>
            <input type="text" id="epg_filter_date_end" style="width:105px;" />
            <label for="epg_filter_to">à :</label>
            <input type="text" id="epg_filter_to" value="23:59" style="width:65px;" />
        </span>
        <div id="epg_weekday_shortcuts" style="display:flex; flex-wrap:wrap; align-items:center; gap:5px; flex-basis:100%; margin:2px 0 4px 0;">
            <span style="font-weight:bold; margin-right:4px;">Jours :</span>
            <button type="button" class="epg-weekday-button" data-weekday="1">Lundi</button>
            <button type="button" class="epg-weekday-button" data-weekday="2">Mardi</button>
            <button type="button" class="epg-weekday-button" data-weekday="3">Mercredi</button>
            <button type="button" class="epg-weekday-button" data-weekday="4">Jeudi</button>
            <button type="button" class="epg-weekday-button" data-weekday="5">Vendredi</button>
            <button type="button" class="epg-weekday-button" data-weekday="6">Samedi</button>
            <button type="button" class="epg-weekday-button" data-weekday="0">Dimanche</button>
        </div>
        <label class="epg-filter-toggle" for="epg_rating_filter_enabled">
            <input type="checkbox" id="epg_rating_filter_enabled" checked="checked" />
            IMDb minimum :
        </label>
        <input type="number" id="epg_rating_min" min="0" max="10" step="0.5" value="6" style="width:58px;" />
        <span>/10</span>
        <label for="epg_group_filter">Groupe M3U :</label>
        <select id="epg_group_filter" class="text ui-widget-content ui-corner-all">
            <option value="">Tous les groupes</option>
            %for group in epggroups:
                %if group[0] == selected_group:
            <option value="{{group[0]}}" selected="selected">{{group[0]}}</option>
                %else:
            <option value="{{group[0]}}">{{group[0]}}</option>
                %end
            %end
        </select>
    </div>
    <h1><div class="lefty">§Electronic Program Guide§ - §list view§</div>
<button id="grabepg" class="grabepgclass">§Import EPG§</button>
<span id="epg_last_import" style="margin-left:10px; font-size:0.85em; font-weight:normal;">Dernier import EPG : {{last_import}}</span>
</h1>
<table id="table_epglist">
    <thead>
        <tr class="ui-widget-header">
            <th class="echan">§Channel§</th>
            <th class="etit">§Title§</th>
            <th class="erating">IMDb</th>
            <th class="edesc">§Description§</th>
            <th class="estart">§Start time§</th>
            <th class="estop">§End time§</th>
            <th class="econtrol">§Record§</th>
        </tr>
    </thead>
    <tbody>
    </tbody>
</table>
</div>
<div id="dialog_record_from_epg" title="§Detail view§" record="§Record§" tunerecord="§Edit&Record§" cancel="§Cancel§">
    <div id="dialog_content"></div>        
</div>
<div id="dialog_remove" title="§Confirmation request§" cancel="§Cancel§" delete="§Delete§"> 
	<p>§Shall I delete this record?§</p>
</div>
<form>
    <input type="text" style="display: none;" name="ret" id="ret" value="X"/>
</form>
export interface paths {
    "/healthz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Check service health
         * @description Report service liveness and the schema version expected by the app.
         */
        get: operations["get_health"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/readyz": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Check database readiness
         * @description Verify that the database is awake and has the expected schema.
         */
        get: operations["get_readiness"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/community-strategies": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List community strategies */
        get: operations["list_community_strategies"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/community-reports/latest": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get the latest community report */
        get: operations["get_latest_community_report"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/community-reports/recent": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List recent community reports */
        get: operations["list_recent_community_reports"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/community-reports/history": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** List older community reports */
        get: operations["list_community_report_history"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/community-reports/{report_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Get a historical community report */
        get: operations["get_community_report"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List seasons
         * @description Return all stored FPL seasons.
         */
        get: operations["list_seasons"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/current": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get the current season
         * @description Return the single current FPL season.
         */
        get: operations["get_current_season"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get a season
         * @description Return one stored FPL season.
         */
        get: operations["get_season"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List season events
         * @description Return all stored FPL events for one season.
         */
        get: operations["list_events"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/events/current": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get the current season event
         * @description Return the single current FPL event for one season.
         */
        get: operations["get_current_event"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/events/{event_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get a season event
         * @description Return one stored FPL event for one season.
         */
        get: operations["get_event"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/phases": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List season phases
         * @description Return all stored FPL phases for one season.
         */
        get: operations["list_phases"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/teams": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List season teams
         * @description Return all stored FPL teams for one season.
         */
        get: operations["list_teams"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/teams/{team_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get a season team
         * @description Return one stored FPL team for one season.
         */
        get: operations["get_team"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/element-types": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List season element types
         * @description Return all stored FPL element types for one season.
         */
        get: operations["list_element_types"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/elements": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List season elements
         * @description Return a cursor page of stored FPL elements.
         */
        get: operations["list_elements"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/elements/{element_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get a season element
         * @description Return one stored FPL element for one season.
         */
        get: operations["get_element"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/fixtures": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List season fixtures
         * @description Return a cursor page of stored FPL fixtures for one season.
         */
        get: operations["list_fixtures"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/events/{event_id}/fixtures": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List season event fixtures
         * @description Return a cursor page of fixtures for one event.
         */
        get: operations["list_event_fixtures"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/event-status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get season event status
         * @description Return latest event-status response from normalised rows.
         */
        get: operations["get_event_status"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/events/{event_id}/live-elements": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List season live elements
         * @description Return a cursor page of live elements.
         */
        get: operations["list_live_elements"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/seasons/{season_id}/events/{event_id}/live-elements/{element_id}": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * Get a season live element
         * @description Return one live element row for one FPL event in one season.
         */
        get: operations["get_live_element"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/change-events/recent": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List recent change events
         * @description Return the newest bounded change-event summaries.
         */
        get: operations["list_recent_change_events"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/change-events/history": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List older change events
         * @description Return a newest-first page before a known event id.
         */
        get: operations["list_change_event_history"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/change-events": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List change events
         * @description Return change-event metadata with identifiers greater than `after_id`, ordered by identifier. Use the last returned identifier to request the next page.
         */
        get: operations["list_change_events"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/change-events/{change_event_id}/entity-changes": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /**
         * List changed entities
         * @description Return field-level entity changes under one family event.
         */
        get: operations["list_entity_changes"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
    "/v1/ingestion-status": {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        /** Inspect automatic ingestion freshness */
        get: operations["get_ingestion_status"];
        put?: never;
        post?: never;
        delete?: never;
        options?: never;
        head?: never;
        patch?: never;
        trace?: never;
    };
}
export type webhooks = Record<string, never>;
export interface components {
    schemas: {
        /**
         * BlogEngagement
         * @description Explicit marker for sources without comparable engagement metrics.
         */
        BlogEngagement: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "blog";
        };
        /**
         * ChangeEventHistoryResponse
         * @description Newest-first page of change-event summaries.
         */
        ChangeEventHistoryResponse: {
            /** Items */
            items: components["schemas"]["ChangeEventResponse"][];
            /** Next Before Id */
            next_before_id: number | null;
        };
        /**
         * ChangeEventResponse
         * @description Public summary of accurate changes within one entity family.
         */
        ChangeEventResponse: {
            /** Id */
            id: number;
            /** Season Id */
            season_id: string;
            entity_family: components["schemas"]["EntityFamily"];
            /** Event Name */
            event_name: string;
            source_key: components["schemas"]["IngestionSourceKey"];
            /** Source Event Id */
            source_event_id: number | null;
            /** Payload Hash */
            payload_hash: string;
            /** Created Count */
            created_count: number;
            /** Updated Count */
            updated_count: number;
            /** Deleted Count */
            deleted_count: number;
            /**
             * Fetched At
             * Format: date-time
             */
            fetched_at: string;
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
        };
        /**
         * ChangeEventsResponse
         * @description Forward cursor page of change-event summaries.
         */
        ChangeEventsResponse: {
            /** Items */
            items: components["schemas"]["ChangeEventResponse"][];
            /** Next After Id */
            next_after_id: number | null;
        };
        /**
         * ChangeKind
         * @description Supported entity-level operations.
         * @enum {string}
         */
        ChangeKind: "created" | "updated" | "deleted";
        /**
         * ChangeValueResponse
         * @description Public field value that distinguishes absence from JSON null.
         */
        ChangeValueResponse: {
            /** Present */
            present: boolean;
            value: components["schemas"]["JsonValue"];
        };
        /**
         * CollectionCoverage
         * @description Collection completeness recorded with a report.
         */
        CollectionCoverage: {
            /** Configured Source Count */
            configured_source_count: number;
            /** Successful Source Count */
            successful_source_count: number;
            /** Failed Sources */
            failed_sources: components["schemas"]["SourceFailure"][];
            /** Excluded Document Count */
            excluded_document_count: number;
            /** Exclusions */
            exclusions: components["schemas"]["SourceExclusion"][];
            /** X Document Count */
            x_document_count: number;
            /** Youtube Document Count */
            youtube_document_count: number;
            /** Blog Document Count */
            blog_document_count: number;
        };
        /**
         * CommunityReport
         * @description One immutable stored community report.
         */
        CommunityReport: {
            /** Id */
            id: number;
            /** Strategy Key */
            strategy_key: string;
            /** Strategy Version */
            strategy_version: number;
            /**
             * Report Date
             * Format: date
             */
            report_date: string;
            /** Season Id */
            season_id: string;
            /** As Of Event Id */
            as_of_event_id: number | null;
            /**
             * Window Start
             * Format: date-time
             */
            window_start: string;
            /**
             * Window End
             * Format: date-time
             */
            window_end: string;
            /**
             * Generated At
             * Format: date-time
             */
            generated_at: string;
            content: components["schemas"]["CommunityReportContent"];
        };
        /**
         * CommunityReportContent
         * @description Single JSON document persisted for one report resource.
         */
        CommunityReportContent: {
            /** Strategy Name */
            strategy_name: string;
            /** Strategy Description */
            strategy_description: string;
            /** Ranking Policy */
            ranking_policy: string;
            /** Extraction Prompt Version */
            extraction_prompt_version: number;
            /** Synthesis Prompt Version */
            synthesis_prompt_version: number;
            /** Target Story Count */
            target_story_count: number;
            coverage: components["schemas"]["CollectionCoverage"];
            extraction_cache: components["schemas"]["ExtractionCacheUsage"];
            model_usage: components["schemas"]["ModelUsage"];
            /** Stories */
            stories: components["schemas"]["CommunityStory"][];
        };
        /**
         * CommunityReportHistoryResponse
         * @description Newest-first page of immutable community report summaries.
         */
        CommunityReportHistoryResponse: {
            /** Items */
            items: components["schemas"]["CommunityReportSummary"][];
            /** Next Before Id */
            next_before_id: number | null;
        };
        /**
         * CommunityReportSummary
         * @description Bounded history item without the complete report JSON.
         */
        CommunityReportSummary: {
            /** Id */
            id: number;
            /** Strategy Key */
            strategy_key: string;
            /** Strategy Version */
            strategy_version: number;
            /**
             * Report Date
             * Format: date
             */
            report_date: string;
            /** Season Id */
            season_id: string;
            /** As Of Event Id */
            as_of_event_id: number | null;
            /**
             * Window Start
             * Format: date-time
             */
            window_start: string;
            /**
             * Window End
             * Format: date-time
             */
            window_end: string;
            /**
             * Generated At
             * Format: date-time
             */
            generated_at: string;
            /** Story Count */
            story_count: number;
            /** Successful Source Count */
            successful_source_count: number;
            /** Failed Source Count */
            failed_source_count: number;
        };
        /**
         * CommunityStory
         * @description One ranked, validated community story.
         */
        CommunityStory: {
            /** Rank */
            rank: number;
            /** Headline */
            headline: string;
            /** Summary */
            summary: string;
            category: components["schemas"]["TopicCategory"];
            /** Momentum Score */
            momentum_score: number;
            momentum_components: components["schemas"]["MomentumComponents"];
            /** Evidence */
            evidence: components["schemas"]["EvidenceReference"][];
            /** Entities */
            entities: (components["schemas"]["PlayerReference"] | components["schemas"]["TeamReference"] | components["schemas"]["EventReference"] | components["schemas"]["FixtureReference"])[];
        };
        /**
         * CommunityStrategySummary
         * @description Public strategy metadata without private source configuration.
         */
        CommunityStrategySummary: {
            /** Key */
            key: string;
            /** Name */
            name: string;
            /** Description */
            description: string;
            /** Cadence */
            cadence: string;
            /** Timezone */
            timezone: string;
            /** Lookback Days */
            lookback_days: number;
            /** Target Story Count */
            target_story_count: number;
        };
        /** CursorPage[Element] */
        CursorPage_Element_: {
            /** Items */
            items: components["schemas"]["Element"][];
            /** Next After Id */
            next_after_id: number | null;
        };
        /** CursorPage[Fixture] */
        CursorPage_Fixture_: {
            /** Items */
            items: components["schemas"]["Fixture"][];
            /** Next After Id */
            next_after_id: number | null;
        };
        /** CursorPage[LiveElement] */
        CursorPage_LiveElement_: {
            /** Items */
            items: components["schemas"]["LiveElement"][];
            /** Next After Id */
            next_after_id: number | null;
        };
        /**
         * Element
         * @description FPL player/element metadata from bootstrap-static.
         */
        Element: {
            /** Id */
            id: number;
            /** Code */
            code?: number | null;
            /** First Name */
            first_name: string;
            /** Second Name */
            second_name: string;
            /** Web Name */
            web_name: string;
            /** Photo */
            photo?: string | null;
            /** Team */
            team: number;
            /** Team Code */
            team_code?: number | null;
            /** Element Type */
            element_type: number;
            /** Status */
            status?: string | null;
            /** News */
            news?: string | null;
            /** News Added */
            news_added?: string | null;
            /** Now Cost */
            now_cost?: number | null;
            /** Selected By Percent */
            selected_by_percent?: string | null;
            /** Total Points */
            total_points?: number | null;
            /** Chance Of Playing Next Round */
            chance_of_playing_next_round?: number | null;
            /** Chance Of Playing This Round */
            chance_of_playing_this_round?: number | null;
            /** Form */
            form?: string | null;
            /** Minutes */
            minutes?: number | null;
            /** Goals Scored */
            goals_scored?: number | null;
            /** Assists */
            assists?: number | null;
            /** Clean Sheets */
            clean_sheets?: number | null;
            /** Goals Conceded */
            goals_conceded?: number | null;
            /** Own Goals */
            own_goals?: number | null;
            /** Penalties Saved */
            penalties_saved?: number | null;
            /** Penalties Missed */
            penalties_missed?: number | null;
            /** Yellow Cards */
            yellow_cards?: number | null;
            /** Red Cards */
            red_cards?: number | null;
            /** Saves */
            saves?: number | null;
            /** Bonus */
            bonus?: number | null;
            /** Bps */
            bps?: number | null;
            /** Influence */
            influence?: string | null;
            /** Creativity */
            creativity?: string | null;
            /** Threat */
            threat?: string | null;
            /** Ict Index */
            ict_index?: string | null;
            /** Expected Goals */
            expected_goals?: string | null;
            /** Expected Assists */
            expected_assists?: string | null;
            /** Expected Goal Involvements */
            expected_goal_involvements?: string | null;
            /** Expected Goals Conceded */
            expected_goals_conceded?: string | null;
        };
        /**
         * ElementType
         * @description FPL player position/element type metadata.
         */
        ElementType: {
            /** Id */
            id: number;
            /** Singular Name */
            singular_name: string;
            /** Singular Name Short */
            singular_name_short?: string | null;
            /** Plural Name */
            plural_name?: string | null;
            /** Plural Name Short */
            plural_name_short?: string | null;
            /** Squad Select */
            squad_select?: number | null;
            /** Squad Min Play */
            squad_min_play?: number | null;
            /** Squad Max Play */
            squad_max_play?: number | null;
            /** Ui Shirt Specific */
            ui_shirt_specific?: boolean | null;
            /** Sub Positions Locked */
            sub_positions_locked?: number[];
            /** Element Count */
            element_count?: number | null;
        };
        /**
         * EntityChangeResponse
         * @description Public field-level changes for one logical entity.
         */
        EntityChangeResponse: {
            /** Id */
            id: number;
            /** Change Event Id */
            change_event_id: number;
            /** Entity Key */
            entity_key: string;
            /** Entity Label */
            entity_label: string;
            kind: components["schemas"]["ChangeKind"];
            /** Fields */
            fields: components["schemas"]["FieldChangeResponse"][];
            /**
             * Created At
             * Format: date-time
             */
            created_at: string;
        };
        /**
         * EntityChangesResponse
         * @description Cursor page of entity changes under one family event.
         */
        EntityChangesResponse: {
            /** Items */
            items: components["schemas"]["EntityChangeResponse"][];
            /** Next After Id */
            next_after_id: number | null;
        };
        /**
         * EntityFamily
         * @description Normalised entity families exposed by change events.
         * @enum {string}
         */
        EntityFamily: "events" | "phases" | "teams" | "element_types" | "element_stats" | "elements" | "fixtures" | "event_status" | "event_live";
        /**
         * ErrorResponse
         * @description Standard FastAPI error payload returned by relay endpoints.
         */
        ErrorResponse: {
            /** Detail */
            detail: string;
        };
        /**
         * Event
         * @description FPL gameweek/event metadata from bootstrap-static.
         */
        Event: {
            /** Id */
            id: number;
            /** Name */
            name: string;
            /** Deadline Time */
            deadline_time?: string | null;
            /** Average Entry Score */
            average_entry_score?: number | null;
            /** Finished */
            finished?: boolean | null;
            /** Data Checked */
            data_checked?: boolean | null;
            /** Highest Scoring Entry */
            highest_scoring_entry?: number | null;
            /** Deadline Time Epoch */
            deadline_time_epoch?: number | null;
            /** Deadline Time Game Offset */
            deadline_time_game_offset?: number | null;
            /** Highest Score */
            highest_score?: number | null;
            /**
             * Is Previous
             * @default false
             */
            is_previous: boolean;
            /**
             * Is Current
             * @default false
             */
            is_current: boolean;
            /**
             * Is Next
             * @default false
             */
            is_next: boolean;
        };
        /** EventReference */
        EventReference: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            entity_type: "event";
            /** Season Id */
            season_id: string;
            /** Entity Id */
            entity_id: number;
            /** Display Name */
            display_name: string;
            snapshot: components["schemas"]["EventSnapshot"];
        };
        /**
         * EventSnapshot
         * @description Gameweek values as they existed when a report was generated.
         */
        EventSnapshot: {
            /** Name */
            name: string;
            /** Deadline Time */
            deadline_time: string | null;
            /** Average Entry Score */
            average_entry_score: number | null;
            /** Highest Score */
            highest_score: number | null;
            /** Highest Scoring Entry */
            highest_scoring_entry: number | null;
            /** Finished */
            finished: boolean | null;
            /** Data Checked */
            data_checked: boolean | null;
            /** Is Previous */
            is_previous: boolean;
            /** Is Current */
            is_current: boolean;
            /** Is Next */
            is_next: boolean;
        };
        /**
         * EventStatusDay
         * @description Per-event/day status row from event-status.
         */
        EventStatusDay: {
            /** Event */
            event: number;
            /** Bonus Added */
            bonus_added: boolean;
            /**
             * Date
             * Format: date
             */
            date: string;
            /** Leagues Updated */
            leagues_updated?: boolean | null;
        };
        /**
         * EventStatusResponse
         * @description Aggregate response from event-status.
         */
        EventStatusResponse: {
            /** Status */
            status?: components["schemas"]["EventStatusDay"][];
            /** Leagues */
            leagues?: string | null;
        };
        /**
         * EvidenceReference
         * @description Auditable source metadata retained without source body text.
         */
        EvidenceReference: {
            /** Document Id */
            document_id: string;
            /** Source Key */
            source_key: string;
            source_type: components["schemas"]["SourceType"];
            /** Publisher */
            publisher: string;
            /** Title */
            title: string;
            /**
             * Url
             * Format: uri
             */
            url: string;
            /**
             * Published At
             * Format: date-time
             */
            published_at: string;
            /** Engagement */
            engagement: components["schemas"]["XEngagement"] | components["schemas"]["YouTubeEngagement"] | components["schemas"]["BlogEngagement"];
        };
        /**
         * ExtractionCacheUsage
         * @description Per-report cache behavior retained for cost review.
         */
        ExtractionCacheUsage: {
            /** Eligible Document Count */
            eligible_document_count: number;
            /** Hit Count */
            hit_count: number;
            /** Miss Count */
            miss_count: number;
            /** Write Count */
            write_count: number;
            /** Expired Entry Count */
            expired_entry_count: number;
        };
        /**
         * FieldChangeResponse
         * @description Public before and after values for one top-level field.
         */
        FieldChangeResponse: {
            /** Field */
            field: string;
            before: components["schemas"]["ChangeValueResponse"];
            after: components["schemas"]["ChangeValueResponse"];
        };
        /**
         * Fixture
         * @description FPL fixture metadata from fixtures endpoints.
         */
        Fixture: {
            /** Id */
            id: number;
            /** Code */
            code?: number | null;
            /** Event */
            event?: number | null;
            /** Finished */
            finished: boolean;
            /** Finished Provisional */
            finished_provisional?: boolean | null;
            /** Kickoff Time */
            kickoff_time?: string | null;
            /** Minutes */
            minutes?: number | null;
            /** Provisional Start Time */
            provisional_start_time?: boolean | null;
            /** Started */
            started: boolean;
            /** Team A */
            team_a: number;
            /** Team A Score */
            team_a_score?: number | null;
            /** Team H */
            team_h: number;
            /** Team H Score */
            team_h_score?: number | null;
            /** Stats */
            stats?: components["schemas"]["FixtureStat"][];
            /** Pulse Id */
            pulse_id?: number | null;
        };
        /** FixtureReference */
        FixtureReference: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            entity_type: "fixture";
            /** Season Id */
            season_id: string;
            /** Entity Id */
            entity_id: number;
            /** Display Name */
            display_name: string;
            snapshot: components["schemas"]["FixtureSnapshot"];
        };
        /**
         * FixtureSnapshot
         * @description Fixture values as they existed when a report was generated.
         */
        FixtureSnapshot: {
            /** Event Id */
            event_id: number | null;
            /** Kickoff Time */
            kickoff_time: string | null;
            /** Home Team Id */
            home_team_id: number;
            /** Home Team Name */
            home_team_name: string;
            /** Away Team Id */
            away_team_id: number;
            /** Away Team Name */
            away_team_name: string;
            /** Home Score */
            home_score: number | null;
            /** Away Score */
            away_score: number | null;
            /** Started */
            started: boolean;
            /** Finished */
            finished: boolean;
        };
        /**
         * FixtureStat
         * @description Fixture statistic split by home and away sides.
         */
        FixtureStat: {
            /** Identifier */
            identifier: string;
            /** A */
            a?: components["schemas"]["FixtureStatEntry"][];
            /** H */
            h?: components["schemas"]["FixtureStatEntry"][];
        };
        /**
         * FixtureStatEntry
         * @description One player/value entry inside a fixture statistic side.
         */
        FixtureStatEntry: {
            value?: components["schemas"]["ScalarValue"];
            /** Element */
            element?: number | null;
        };
        /** HTTPValidationError */
        HTTPValidationError: {
            /** Detail */
            detail?: components["schemas"]["ValidationError"][];
        };
        /**
         * HealthResponse
         * @description Service liveness and expected database schema version.
         */
        HealthResponse: {
            /**
             * Status
             * @constant
             */
            status: "ok";
            /** Schema Version */
            schema_version: number;
        };
        /**
         * IngestionSourceKey
         * @description Upstream source identifiers used for latest ingestion metadata.
         * @enum {string}
         */
        IngestionSourceKey: "bootstrap-static" | "fixtures" | "fixtures-current-event" | "event-status" | "event-live";
        /**
         * IngestionStatusResponse
         * @description Reference and live automatic-ingestion freshness.
         */
        IngestionStatusResponse: {
            /** Season Id */
            season_id: string | null;
            /**
             * Checked At
             * Format: date-time
             */
            checked_at: string;
            reference: components["schemas"]["PipelineStatusResponse"];
            live: components["schemas"]["PipelineStatusResponse"];
        };
        JsonValue: unknown;
        /**
         * LiveElement
         * @description Live gameweek state for one FPL player/element.
         */
        LiveElement: {
            /** Id */
            id: number;
            stats: components["schemas"]["LiveElementStats"];
            /** Explain */
            explain?: components["schemas"]["LiveElementExplain"][];
        };
        /**
         * LiveElementExplain
         * @description Fixture-level live points explanation for one element.
         */
        LiveElementExplain: {
            /** Fixture */
            fixture: number;
            /** Stats */
            stats?: components["schemas"]["LiveElementExplainStat"][];
        };
        /**
         * LiveElementExplainStat
         * @description One live points explanation stat row.
         */
        LiveElementExplainStat: {
            /** Identifier */
            identifier: string;
            /** Points */
            points: number;
            value?: components["schemas"]["ScalarValue"];
        };
        /**
         * LiveElementStats
         * @description Live aggregate stats for one FPL element in a gameweek.
         */
        LiveElementStats: {
            /** Minutes */
            minutes?: number | null;
            /** Goals Scored */
            goals_scored?: number | null;
            /** Assists */
            assists?: number | null;
            /** Clean Sheets */
            clean_sheets?: number | null;
            /** Goals Conceded */
            goals_conceded?: number | null;
            /** Own Goals */
            own_goals?: number | null;
            /** Penalties Saved */
            penalties_saved?: number | null;
            /** Penalties Missed */
            penalties_missed?: number | null;
            /** Yellow Cards */
            yellow_cards?: number | null;
            /** Red Cards */
            red_cards?: number | null;
            /** Saves */
            saves?: number | null;
            /** Bonus */
            bonus?: number | null;
            /** Bps */
            bps?: number | null;
            /** Influence */
            influence?: string | null;
            /** Creativity */
            creativity?: string | null;
            /** Threat */
            threat?: string | null;
            /** Ict Index */
            ict_index?: string | null;
            /** Starts */
            starts?: number | null;
            /** Expected Goals */
            expected_goals?: string | null;
            /** Expected Assists */
            expected_assists?: string | null;
            /** Expected Goal Involvements */
            expected_goal_involvements?: string | null;
            /** Expected Goals Conceded */
            expected_goals_conceded?: string | null;
            /** Defensive Contribution */
            defensive_contribution?: number | null;
            /** Total Points */
            total_points?: number | null;
            /** In Dreamteam */
            in_dreamteam?: boolean | null;
        };
        /**
         * ModelUsage
         * @description Aggregate OpenAI usage retained for operations and cost review.
         */
        ModelUsage: {
            /**
             * Provider
             * @constant
             */
            provider: "openai";
            /** Model */
            model: string;
            /** Reasoning Effort */
            reasoning_effort: string;
            /** Response Ids */
            response_ids: string[];
            /** Input Tokens */
            input_tokens: number;
            /** Output Tokens */
            output_tokens: number;
        };
        /**
         * MomentumComponents
         * @description Explainable component scores that add to the story score.
         */
        MomentumComponents: {
            /** Source Breadth */
            source_breadth: number;
            /** Evidence Volume */
            evidence_volume: number;
            /** Engagement */
            engagement: number;
            /** Recency */
            recency: number;
            /** Actionability */
            actionability: number;
        };
        /**
         * Phase
         * @description FPL phase metadata from bootstrap-static.
         */
        Phase: {
            /** Id */
            id: number;
            /** Name */
            name: string;
            /** Start Event */
            start_event: number;
            /** Stop Event */
            stop_event: number;
        };
        /**
         * PipelineStatusResponse
         * @description Freshness status for one automatic ingestion stream.
         */
        PipelineStatusResponse: {
            /**
             * State
             * @enum {string}
             */
            state: "initializing" | "healthy" | "stale" | "idle" | "polling";
            /** Expected Interval Seconds */
            expected_interval_seconds: number;
            /** Stale After Seconds */
            stale_after_seconds: number;
            /** Last Checked At */
            last_checked_at: string | null;
            /** Last Changed At */
            last_changed_at: string | null;
            /** Current Window End */
            current_window_end: string | null;
            /** Next Window Start */
            next_window_start: string | null;
        };
        /** PlayerReference */
        PlayerReference: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            entity_type: "player";
            /** Season Id */
            season_id: string;
            /** Entity Id */
            entity_id: number;
            /** Display Name */
            display_name: string;
            snapshot: components["schemas"]["PlayerSnapshot"];
        };
        /**
         * PlayerSnapshot
         * @description Player values as they existed when a report was generated.
         */
        PlayerSnapshot: {
            /** Web Name */
            web_name: string;
            /** First Name */
            first_name: string;
            /** Second Name */
            second_name: string;
            /** Team Id */
            team_id: number;
            /** Team Name */
            team_name: string;
            /** Element Type Id */
            element_type_id: number;
            /** Element Type Name */
            element_type_name: string;
            /** Now Cost */
            now_cost: number | null;
            /** Selected By Percent */
            selected_by_percent: string | null;
            /** Total Points */
            total_points: number | null;
            /** Form */
            form: string | null;
            /** Minutes */
            minutes: number | null;
            /** Goals Scored */
            goals_scored: number | null;
            /** Assists */
            assists: number | null;
            /** Clean Sheets */
            clean_sheets: number | null;
            /** Status */
            status: string | null;
            /** News */
            news: string | null;
            /** Chance Of Playing Next Round */
            chance_of_playing_next_round: number | null;
            /** Chance Of Playing This Round */
            chance_of_playing_this_round: number | null;
        };
        /**
         * ReadyResponse
         * @description Database readiness and applied schema version.
         */
        ReadyResponse: {
            /**
             * Status
             * @constant
             */
            status: "ready";
            /** Schema Version */
            schema_version: number;
        };
        ScalarValue: number | string | boolean | null;
        /**
         * Season
         * @description Derived relay season metadata for one active FPL season.
         */
        Season: {
            /** Id */
            id: string;
            /** Start Year */
            start_year: number;
            /** End Year */
            end_year: number;
            /**
             * First Deadline Time
             * Format: date-time
             */
            first_deadline_time: string;
            /**
             * Last Deadline Time
             * Format: date-time
             */
            last_deadline_time: string;
            /** Is Current */
            is_current: boolean;
        };
        /**
         * ServiceErrorResponse
         * @description Stable machine-readable service availability error.
         */
        ServiceErrorResponse: {
            /** Detail */
            detail: string;
            /**
             * Code
             * @enum {string}
             */
            code: "database_waking" | "database_unavailable" | "schema_unavailable";
            /** Retry After Seconds */
            retry_after_seconds: number | null;
        };
        /**
         * SourceExclusion
         * @description Stable reason and count for documents intentionally not analyzed.
         */
        SourceExclusion: {
            /** Source Key */
            source_key: string;
            source_type: components["schemas"]["SourceType"];
            /** Code */
            code: string;
            /** Count */
            count: number;
        };
        /**
         * SourceFailure
         * @description Stable, safe record of a source-level collection failure.
         */
        SourceFailure: {
            /** Source Key */
            source_key: string;
            source_type: components["schemas"]["SourceType"];
            /** Code */
            code: string;
        };
        /**
         * SourceType
         * @description Supported public community media sources.
         * @enum {string}
         */
        SourceType: "x" | "youtube" | "blog";
        /**
         * Team
         * @description Premier League team metadata from bootstrap-static.
         */
        Team: {
            /** Id */
            id: number;
            /** Name */
            name: string;
            /** Short Name */
            short_name: string;
            /** Code */
            code?: number | null;
            /** Strength */
            strength?: number | null;
            /** Strength Overall Home */
            strength_overall_home?: number | null;
            /** Strength Overall Away */
            strength_overall_away?: number | null;
            /** Strength Attack Home */
            strength_attack_home?: number | null;
            /** Strength Attack Away */
            strength_attack_away?: number | null;
            /** Strength Defence Home */
            strength_defence_home?: number | null;
            /** Strength Defence Away */
            strength_defence_away?: number | null;
            /** Pulse Id */
            pulse_id?: number | null;
        };
        /** TeamReference */
        TeamReference: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            entity_type: "team";
            /** Season Id */
            season_id: string;
            /** Entity Id */
            entity_id: number;
            /** Display Name */
            display_name: string;
            snapshot: components["schemas"]["TeamSnapshot"];
        };
        /**
         * TeamSnapshot
         * @description Team values as they existed when a report was generated.
         */
        TeamSnapshot: {
            /** Name */
            name: string;
            /** Short Name */
            short_name: string;
            /** Strength */
            strength: number | null;
            /** Strength Overall Home */
            strength_overall_home: number | null;
            /** Strength Overall Away */
            strength_overall_away: number | null;
            /** Strength Attack Home */
            strength_attack_home: number | null;
            /** Strength Attack Away */
            strength_attack_away: number | null;
            /** Strength Defence Home */
            strength_defence_home: number | null;
            /** Strength Defence Away */
            strength_defence_away: number | null;
        };
        /**
         * TopicCategory
         * @description FPL decision categories used by analysis and presentation.
         * @enum {string}
         */
        TopicCategory: "transfer_in" | "transfer_out" | "hold" | "sell" | "captaincy" | "injury" | "fixture" | "chip" | "team" | "other";
        /** ValidationError */
        ValidationError: {
            /** Location */
            loc: (string | number)[];
            /** Message */
            msg: string;
            /** Error Type */
            type: string;
            /** Input */
            input?: unknown;
            /** Context */
            ctx?: Record<string, never>;
        };
        /**
         * XEngagement
         * @description Public X engagement captured with one post.
         */
        XEngagement: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "x";
            /** Likes */
            likes: number;
            /** Replies */
            replies: number;
            /** Reposts */
            reposts: number;
            /** Quotes */
            quotes: number;
        };
        /**
         * YouTubeEngagement
         * @description Public YouTube engagement captured with one video.
         */
        YouTubeEngagement: {
            /**
             * @description discriminator enum property added by openapi-typescript
             * @enum {string}
             */
            type: "youtube";
            /** Views */
            views: number;
            /** Likes */
            likes: number;
            /** Comments */
            comments: number;
        };
    };
    responses: never;
    parameters: never;
    requestBodies: never;
    headers: never;
    pathItems: never;
}
export type $defs = Record<string, never>;
export interface operations {
    get_health: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Service liveness and expected schema version. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HealthResponse"];
                };
            };
        };
    };
    get_readiness: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Database readiness and applied schema version. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ReadyResponse"];
                };
            };
            /** @description Service Unavailable */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ServiceErrorResponse"];
                };
            };
        };
    };
    list_community_strategies: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommunityStrategySummary"][];
                };
            };
        };
    };
    get_latest_community_report: {
        parameters: {
            query: {
                /** @description Configured strategy key. */
                strategy_key: string;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommunityReport"];
                };
            };
            /** @description The requested entity does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description The requested data has not been ingested yet. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    list_recent_community_reports: {
        parameters: {
            query: {
                strategy_key: string;
                limit: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommunityReportHistoryResponse"];
                };
            };
            /** @description The requested entity does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description The requested data has not been ingested yet. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    list_community_report_history: {
        parameters: {
            query: {
                strategy_key: string;
                before_id: number;
                limit: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommunityReportHistoryResponse"];
                };
            };
            /** @description The requested entity does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description The requested data has not been ingested yet. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    get_community_report: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                report_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CommunityReport"];
                };
            };
            /** @description The requested entity does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_seasons: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description All stored FPL seasons. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Season"][];
                };
            };
        };
    };
    get_current_season: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The season marked as current by relay ingestion. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Season"];
                };
            };
            /** @description The requested data has not been ingested yet. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    get_season: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The requested FPL season. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Season"];
                };
            };
            /** @description The requested entity does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_events: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description All stored FPL events for the requested season. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Event"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_current_event: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The event marked as current for the requested season. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Event"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description The requested data has not been ingested yet. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    get_event: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
                /** @description FPL event identifier. */
                event_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The requested FPL event. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Event"];
                };
            };
            /** @description The requested entity does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_phases: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description All stored FPL phases for the requested season. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Phase"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_teams: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description All stored Premier League teams for the season. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Team"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_team: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
                /** @description FPL team identifier. */
                team_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The requested Premier League team. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Team"];
                };
            };
            /** @description The requested entity does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_element_types: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description All stored FPL player position definitions. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ElementType"][];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_elements: {
        parameters: {
            query: {
                after_id: number;
                limit: number;
            };
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description All stored FPL players for the requested season. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorPage_Element_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_element: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
                /** @description FPL element/player identifier. */
                element_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The requested FPL player. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["Element"];
                };
            };
            /** @description The requested entity does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_fixtures: {
        parameters: {
            query: {
                after_id: number;
                limit: number;
            };
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description All stored FPL fixtures for the requested season. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorPage_Fixture_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_event_fixtures: {
        parameters: {
            query: {
                after_id: number;
                limit: number;
            };
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
                /** @description FPL event identifier. */
                event_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Stored fixtures assigned to the requested FPL event. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorPage_Fixture_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_event_status: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The latest stored FPL event-status response. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EventStatusResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
            /** @description The requested data has not been ingested yet. */
            503: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
        };
    };
    list_live_elements: {
        parameters: {
            query: {
                after_id: number;
                limit: number;
            };
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
                /** @description FPL event identifier. */
                event_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Live player rows for the requested FPL event. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["CursorPage_LiveElement_"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_live_element: {
        parameters: {
            query?: never;
            header?: never;
            path: {
                /** @description FPL season id. */
                season_id: string;
                /** @description FPL event identifier. */
                event_id: number;
                /** @description FPL element/player identifier. */
                element_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description The requested player's live row for an FPL event. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["LiveElement"];
                };
            };
            /** @description The requested entity does not exist. */
            404: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ErrorResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_recent_change_events: {
        parameters: {
            query: {
                /** @description Maximum events to return. */
                limit: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChangeEventHistoryResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_change_event_history: {
        parameters: {
            query: {
                /** @description Return events older than this id. */
                before_id: number;
                /** @description Maximum events to return. */
                limit: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChangeEventHistoryResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_change_events: {
        parameters: {
            query: {
                /** @description Return events after this change-event identifier. */
                after_id: number;
                /** @description Maximum number of events to return. */
                limit: number;
            };
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description A page of stored change events. */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["ChangeEventsResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    list_entity_changes: {
        parameters: {
            query: {
                /** @description Return entity changes after this id. */
                after_id: number;
                /** @description Maximum changes to return. */
                limit: number;
            };
            header?: never;
            path: {
                /** @description Stored change-event identifier. */
                change_event_id: number;
            };
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["EntityChangesResponse"];
                };
            };
            /** @description Validation Error */
            422: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["HTTPValidationError"];
                };
            };
        };
    };
    get_ingestion_status: {
        parameters: {
            query?: never;
            header?: never;
            path?: never;
            cookie?: never;
        };
        requestBody?: never;
        responses: {
            /** @description Successful Response */
            200: {
                headers: {
                    [name: string]: unknown;
                };
                content: {
                    "application/json": components["schemas"]["IngestionStatusResponse"];
                };
            };
        };
    };
}

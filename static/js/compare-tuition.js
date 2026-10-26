// International tuition comparison.
// Tuition, scholarship and deposit calculations come from the API.

let allResults = [];
let allResultsUnfiltered = [];

let exchangeRates = { GBP: 1 };
let currencySymbols = { GBP: "£" };
let userCurrency = "GBP";

let debounceTimer;
let subjectRequestNumber = 0;
let comparisonRequestNumber = 0;


function escapeHtml(value) {
    return String(value ?? "").replace(/[&<>"']/g, character => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;"
    })[character]);
}


function hasAmount(value) {
    return (
        value !== null
        && value !== undefined
        && String(value).trim() !== ""
        && Number.isFinite(Number(value))
    );
}


async function loadExchangeRates() {
    try {
        const response = await fetch("/api/exchange-rates/");

        if (!response.ok) {
            throw new Error("Exchange-rate request failed.");
        }

        const data = await response.json();

        // This endpoint's rates are expected to use GBP as their base.
        const base = String(
            data.base || data.base_code || "GBP"
        ).toUpperCase();

        if (base !== "GBP") {
            throw new Error("The exchange-rate base is not GBP.");
        }

        if (
            data.rates
            && typeof data.rates === "object"
            && !Array.isArray(data.rates)
        ) {
            exchangeRates = { ...data.rates, GBP: 1 };
        }

        if (
            data.symbols
            && typeof data.symbols === "object"
            && !Array.isArray(data.symbols)
        ) {
            currencySymbols = { ...data.symbols, GBP: "£" };
        }
    } catch (error) {
        console.warn("Currency conversion unavailable:", error);
    }
}


function updateUserCurrency(countryCode) {
    const currencies = {
        NG: "NGN",
        GH: "GHS",
        KE: "KES",
        ZA: "ZAR",
        US: "USD",
        IN: "INR",
        CN: "CNY",
        PK: "PKR",
        BD: "BDT",
        AF: "AFN",
        IR: "IRR",
        IE: "EUR",
        DE: "EUR",
        FR: "EUR",
        ES: "EUR",
        OTHER: "USD"
    };

    const country = String(countryCode || "").toUpperCase();
    userCurrency = currencies[country] || "GBP";
}


function convertCurrency(amount, fromCurrency, toCurrency) {
    if (!hasAmount(amount)) {
        return null;
    }

    const value = Number(amount);

    if (fromCurrency === toCurrency) {
        return value;
    }

    const from = Number(exchangeRates[fromCurrency]);
    const to = Number(exchangeRates[toCurrency]);

    if (
        !Number.isFinite(from)
        || !Number.isFinite(to)
        || from <= 0
        || to <= 0
    ) {
        return null;
    }

    return value / from * to;
}


function formatCurrency(amount, currency) {
    if (!hasAmount(amount)) {
        return "Unavailable";
    }

    const symbol = escapeHtml(
        currencySymbols[currency] || `${currency} `
    );

    const formatted = Number(amount).toLocaleString(undefined, {
        minimumFractionDigits: 2,
        maximumFractionDigits: 2
    });

    return `${symbol}${formatted}`;
}


function convertedAmountHtml(amount, currency) {
    if (currency === userCurrency) {
        return "";
    }

    const converted = convertCurrency(
        amount,
        currency,
        userCurrency
    );

    if (converted === null) {
        return "";
    }

    return `
        <p class="tuition-card-note">
            ≈ ${formatCurrency(converted, userCurrency)}
        </p>
    `;
}


function feeIsAvailable(result) {
    return (
        result.fee_available !== false
        && hasAmount(result.tuition_fee)
        && hasAmount(result.net_tuition)
        && Number(result.tuition_fee) >= 0
        && Number(result.net_tuition) >= 0
    );
}


function compareFees(a, b, descending = false) {
    const aAvailable = feeIsAvailable(a);
    const bAvailable = feeIsAvailable(b);

    if (aAvailable !== bAvailable) {
        return aAvailable ? -1 : 1;
    }

    if (!aAvailable) {
        return 0;
    }

    // Different currencies remain grouped rather than directly compared.
    const currencyOrder = String(a.currency || "").localeCompare(
        String(b.currency || "")
    );

    if (currencyOrder !== 0) {
        return currencyOrder;
    }

    return descending
        ? Number(b.net_tuition) - Number(a.net_tuition)
        : Number(a.net_tuition) - Number(b.net_tuition);
}


function showResultsSection() {
    document.getElementById("comparison-results")
        ?.classList.remove("hidden");
}


function setResultCount(message) {
    const count = document.getElementById("comparison-count");

    if (count) {
        count.textContent = message;
    }
}


function showLoading() {
    showResultsSection();
    setResultCount("Loading courses…");

    const grid = document.getElementById("comparison-grid");

    if (grid) {
        grid.setAttribute("aria-busy", "true");
        grid.innerHTML = `
            <div class="tuition-result-message" role="status">
                Loading tuition comparison…
            </div>
        `;
    }
}


function showNoResults() {
    showResultsSection();
    setResultCount("0 course options found");

    const grid = document.getElementById("comparison-grid");

    if (grid) {
        grid.setAttribute("aria-busy", "false");
        grid.innerHTML = `
            <div class="tuition-result-message">
                <h3>No Results Found</h3>
                <p>No courses match these filters.</p>
            </div>
        `;
    }
}


function showError(message) {
    showResultsSection();
    setResultCount("Unable to load results");

    const grid = document.getElementById("comparison-grid");

    if (grid) {
        grid.setAttribute("aria-busy", "false");
        grid.innerHTML = `
            <div
                class="tuition-result-message tuition-error"
                role="alert"
            >
                ${escapeHtml(message)}
            </div>
        `;
    }
}


function invalidateComparison() {
    // Any pending response from the old answers must be ignored.
    comparisonRequestNumber += 1;

    allResults = [];
    allResultsUnfiltered = [];

    document.getElementById("comparison-results")
        ?.classList.add("hidden");

    const grid = document.getElementById("comparison-grid");

    if (grid) {
        grid.replaceChildren();
        grid.setAttribute("aria-busy", "false");
    }

    setResultCount("Answers changed. Select Compare Fees.");
}


function createDepositHtml(result, currency) {
    const deposit = result.deposit_calculation || {};
    const depositCurrency = String(deposit.currency || currency);

    const available = (
        deposit.available === true
        && hasAmount(deposit.amount)
        && Number(deposit.amount) >= 0
    );

    if (!available) {
        return `
            <p class="tuition-card-label">
                ${escapeHtml(
                    deposit.label || "Deposit assessment required"
                )}
            </p>

            <p class="tuition-card-note">
                ${escapeHtml(
                    deposit.note
                    || "Confirm deposit requirements with the university."
                )}
            </p>
        `;
    }

    return `
        <p class="tuition-card-label">
            ${escapeHtml(
                deposit.label || "Estimated deposit before CAS"
            )}
        </p>

        <p class="tuition-card-amount">
            ${formatCurrency(deposit.amount, depositCurrency)}
        </p>

        <p class="tuition-card-note">
            ${escapeHtml(deposit.note || "")}
        </p>
    `;
}


function createComparisonCard(result) {
    const university = escapeHtml(result.university_name);
    const title = escapeHtml(result.course_title);

    const courseId = encodeURIComponent(result.course_id || "");
    const slug = encodeURIComponent(result.university_slug || "");

    const detailsUrl = `/courses/${courseId}/`;
    const universityUrl = `/universities/${slug}/`;

    if (!feeIsAvailable(result)) {
        return `
            <article class="tuition-card">
                <div class="tuition-card-header">
                    <h3>${university}</h3>
                    <p class="tuition-card-note">
                        ${escapeHtml(result.location || "")}
                    </p>
                </div>

                <div class="tuition-card-section">
                    <p class="tuition-card-title">${title}</p>

                    <p class="tuition-card-label">
                        ${escapeHtml(
                            result.fee_label
                            || "Fee verification required"
                        )}
                    </p>

                    <p class="tuition-card-note">
                        ${escapeHtml(
                            result.fee_note
                            || "The published fee needs confirmation."
                        )}
                    </p>

                    <p class="tuition-card-note">
                        No scholarship or deposit amount calculated
                        without an available fee.
                    </p>
                </div>

                <a class="tuition-card-link" href="${detailsUrl}">
                    View Course Details
                </a>
            </article>
        `;
    }

    const currency = String(result.currency || "GBP");
    const calculation = result.scholarship_calculation || {};
    const maximum = calculation.maximum_award_estimate === true;

    const scholarship = hasAmount(result.scholarship)
        ? Number(result.scholarship)
        : 0;

    const year = /^\d{4}\/\d{2}$/.test(
        String(result.academic_year || "")
    )
        ? String(result.academic_year)
        : "";

    const reviews = Array.isArray(calculation.review)
        ? calculation.review
        : [];

    const notes = Array.isArray(calculation.notes)
        ? calculation.notes
        : [];

    const verificationText = result.fee_verified === true
        ? "Published fee verified"
        : "Stored fee — source verification pending";

    const scholarshipHtml = scholarship > 0
        ? `
            <div class="tuition-card-section">
                <p class="tuition-card-label tuition-card-discount">
                    ${maximum
                        ? "Maximum potential scholarship"
                        : "Tuition reduction"}
                </p>

                <p>
                    ${maximum ? "Up to " : ""}
                    ${formatCurrency(scholarship, currency)}
                </p>

                <p class="tuition-card-note">
                    ${escapeHtml(result.scholarship_info || "")}
                </p>
            </div>
        `
        : `
            <div class="tuition-card-section">
                <p class="tuition-card-note">
                    ${escapeHtml(
                        result.scholarship_info
                        || "No verified award applied"
                    )}
                </p>
            </div>
        `;

    const notesHtml = notes.map(note => {
        const text = typeof note === "string"
            ? note
            : note?.reason || "";

        return text
            ? `<p class="tuition-card-note">${escapeHtml(text)}</p>`
            : "";
    }).join("");

    return `
        <article class="tuition-card">
            <div class="tuition-card-header">
                <h3>
                    <a href="${universityUrl}">${university}</a>
                </h3>

                <p class="tuition-card-note">
                    ${escapeHtml(result.location || "")}
                </p>
            </div>

            <div class="tuition-card-section">
                <p class="tuition-card-title">${title}</p>

                <p class="tuition-card-note">
                    ${escapeHtml(result.duration || "")}
                    • Starts ${escapeHtml(result.start_date || "")}
                </p>
            </div>

            <div class="tuition-card-section">
                <p class="tuition-card-label">
                    ${escapeHtml(result.fee_label || "Tuition fee")}
                    ${year ? `(${escapeHtml(year)})` : ""}
                </p>

                <p class="tuition-card-amount">
                    ${formatCurrency(result.tuition_fee, currency)}
                </p>

                ${convertedAmountHtml(result.tuition_fee, currency)}

                <p class="tuition-card-note">
                    ${verificationText}
                </p>

                <p class="tuition-card-note">
                    ${escapeHtml(result.fee_note || "")}
                </p>
            </div>

            ${scholarshipHtml}

            <div class="tuition-card-section">
                <p class="tuition-card-label">
                    ${escapeHtml(
                        calculation.label || "Net tuition fee"
                    )}
                </p>

                <p class="tuition-card-amount">
                    ${formatCurrency(result.net_tuition, currency)}
                </p>

                ${convertedAmountHtml(result.net_tuition, currency)}
            </div>

            <div class="tuition-card-section">
                ${createDepositHtml(result, currency)}
            </div>

            ${reviews.length > 0 ? `
                <p class="tuition-card-note">
                    ${reviews.length} award(s) were not deducted;
                    eligibility or rules require confirmation.
                </p>
            ` : ""}

            ${notesHtml}

            <a class="tuition-card-link" href="${detailsUrl}">
                View Course Details
            </a>
        </article>
    `;
}


function displayResults(results) {
    if (!Array.isArray(results) || results.length === 0) {
        showNoResults();
        return;
    }

    showResultsSection();

    setResultCount(
        `${results.length} course ${
            results.length === 1 ? "option" : "options"
        } found`
    );

    const grid = document.getElementById("comparison-grid");

    if (!grid) {
        console.error("Missing element: #comparison-grid");
        return;
    }

    grid.setAttribute("aria-busy", "false");
    grid.innerHTML = results.map(createComparisonCard).join("");
}


function filterResultsByLevel(level) {
    const selected = String(level || "").trim().toUpperCase();

    const groups = {
        UG: ["UG", "TOPUP"],
        UNDERGRADUATE: ["UG", "TOPUP"],
        PG: ["PG", "PGT", "MRES"],
        POSTGRADUATE: ["PG", "PGT", "MRES"],
        PGT: ["PG", "PGT"],
        TOPUP: ["TOPUP"],
        "TOP-UP": ["TOPUP"],
        MRES: ["MRES"]
    };

    if (["", "ALL", "ALL LEVELS"].includes(selected)) {
        allResults = [...allResultsUnfiltered];
    } else {
        const allowed = groups[selected] || [selected];

        allResults = allResultsUnfiltered.filter(result =>
            allowed.includes(
                String(result.course_level || "").toUpperCase()
            )
        );
    }

    const sortBy = document.getElementById("sort-filter")
        ?.value || "lowest";

    sortResults(sortBy);
}


function sortResults(sortBy) {
    allResults.sort(
        (a, b) => compareFees(a, b, sortBy === "highest")
    );

    displayResults(allResults);
}


async function handleFormSubmit(event) {
    event.preventDefault();

    const form = event.currentTarget;

    if (!form.reportValidity()) {
        return;
    }

    const requestNumber = ++comparisonRequestNumber;
    const formData = new FormData(form);

    const residence = String(
        formData.get("residence") || ""
    ).trim().toUpperCase();

    const destination = String(
        formData.get("destination") || ""
    ).trim().toUpperCase();

    const programme = String(
        formData.get("programme") || ""
    ).trim().toUpperCase();

    const subject = String(
        formData.get("subject") || ""
    ).trim();

    if (!destination || !programme || !subject) {
        showError(
            "Select a destination, programme and subject."
        );
        return;
    }

    allResults = [];
    allResultsUnfiltered = [];

    updateUserCurrency(residence);
    showLoading();

    const params = new URLSearchParams({
        residence,
        destination,
        programme,
        subject,
        fee_status: "international"
    });

    for (const key of [
        "offer_received",
        "applied_by_deadline",
        "external_funding",
        "new_entrant",
        "full_time",
        "on_campus_dundee",
        "academic_conditions_met",
        "partner_discount",
        "repeat_year",
        "pg_2_1_equivalent_met",
        "requires_student_visa",
        "self_funded"
    ]) {
        const answer = String(formData.get(key) || "").trim();

        // Missing answers remain unknown.
        if (answer === "yes" || answer === "no") {
            params.set(key, answer);
        }
    }

    const residenceRegion = String(
        formData.get("residence_region") || ""
    ).trim();

    if (residence === "IN" && residenceRegion) {
        params.set("residence_region", residenceRegion);
    }

    try {
        const response = await fetch(
            `/api/compare-tuition/?${params.toString()}`
        );

        const data = await response.json();

        if (requestNumber !== comparisonRequestNumber) {
            return;
        }

        if (!response.ok) {
            throw new Error(
                data.error || `Request failed (${response.status}).`
            );
        }

        if (!Array.isArray(data.results)) {
            throw new Error("The API returned an unexpected response.");
        }

        allResultsUnfiltered = [...data.results];
        allResults = [...data.results];

        const levelFilter = document.getElementById("level-filter");

        if (levelFilter) {
            levelFilter.value = "";
        }

        const sortBy = document.getElementById("sort-filter")
            ?.value || "lowest";

        sortResults(sortBy);

    } catch (error) {
        if (requestNumber !== comparisonRequestNumber) {
            return;
        }

        console.error("Tuition comparison failed:", error);

        showError(
            error.message || "Unable to load tuition comparison."
        );
    }
}


function selectSubject(code, name) {
    const input = document.getElementById("subject");

    if (input) {
        input.value = String(name || "");
        input.dataset.subjectCode = String(code || "");
    }

    document.getElementById("subject-suggestions")
        ?.classList.add("hidden");

    invalidateComparison();
}


function displaySubjectSuggestions(subjects) {
    const container = document.getElementById("subject-suggestions");

    if (!container) {
        return;
    }

    container.replaceChildren();
    container.classList.remove("hidden");

    if (!Array.isArray(subjects) || subjects.length === 0) {
        const message = document.createElement("p");
        message.className = "tuition-suggestion-message";
        message.textContent = "No subjects found";
        container.appendChild(message);
        return;
    }

    for (const subject of subjects) {
        const button = document.createElement("button");

        button.type = "button";
        button.className = "tuition-suggestion";
        button.textContent = String(subject.name || "");

        button.addEventListener("click", () => {
            subjectRequestNumber += 1;
            clearTimeout(debounceTimer);

            selectSubject(subject.subject_code, subject.name);
        });

        container.appendChild(button);
    }
}


async function fetchSubjects(query, requestNumber) {
    try {
        const response = await fetch(
            `/api/subjects/?q=${encodeURIComponent(query)}`
        );

        if (!response.ok) {
            throw new Error("Subject search failed.");
        }

        const data = await response.json();

        if (requestNumber !== subjectRequestNumber) {
            return;
        }

        displaySubjectSuggestions(data.subjects);

    } catch (error) {
        if (requestNumber === subjectRequestNumber) {
            document.getElementById("subject-suggestions")
                ?.classList.add("hidden");
        }

        console.warn("Subject search unavailable:", error);
    }
}


function setupSubjectAutocomplete() {
    const input = document.getElementById("subject");
    const suggestions = document.getElementById("subject-suggestions");

    if (!input || !suggestions) {
        return;
    }

    input.addEventListener("input", () => {
        clearTimeout(debounceTimer);

        const requestNumber = ++subjectRequestNumber;
        const query = input.value.trim();

        delete input.dataset.subjectCode;

        if (query.length < 2) {
            suggestions.classList.add("hidden");
            return;
        }

        debounceTimer = setTimeout(
            () => fetchSubjects(query, requestNumber),
            300
        );
    });

    input.addEventListener("keydown", event => {
        if (event.key === "Escape") {
            subjectRequestNumber += 1;
            clearTimeout(debounceTimer);
            suggestions.classList.add("hidden");
        }
    });

    document.addEventListener("click", event => {
        if (
            !input.contains(event.target)
            && !suggestions.contains(event.target)
        ) {
            subjectRequestNumber += 1;
            clearTimeout(debounceTimer);
            suggestions.classList.add("hidden");
        }
    });
}


function setupDepositInputs() {
    const residence = document.getElementById("residence-country");
    const regionField = document.getElementById(
        "residence-region-field"
    );
    const regionInput = document.getElementById("residence-region");

    function updateRegionVisibility() {
        const isIndia = (
            String(residence?.value || "").toUpperCase() === "IN"
        );

        if (regionField) {
            regionField.hidden = !isIndia;
        }

        if (regionInput) {
            regionInput.disabled = !isIndia;

            if (!isIndia) {
                regionInput.value = "";
            }
        }
    }

    updateRegionVisibility();

    residence?.addEventListener("change", () => {
        updateRegionVisibility();
        updateUserCurrency(residence.value);
    });
}


function initializeTuitionComparison() {
    const form = document.getElementById("tuition-compare-form");

    if (!form) {
        return;
    }

    form.addEventListener("submit", handleFormSubmit);

    // Old results must not remain visible after answers change.
    form.addEventListener("input", invalidateComparison);
    form.addEventListener("change", invalidateComparison);

    const levelFilter = document.getElementById("level-filter");

    levelFilter?.addEventListener("change", () => {
        filterResultsByLevel(levelFilter.value);
    });

    document.getElementById("level-search-btn")
        ?.addEventListener("click", () => {
            filterResultsByLevel(levelFilter?.value || "");
        });

    document.getElementById("sort-filter")
        ?.addEventListener("change", event => {
            sortResults(event.target.value);
        });

    updateUserCurrency(
        document.getElementById("residence-country")?.value
    );

    setupDepositInputs();
    setupSubjectAutocomplete();

    // Comparing fees does not wait for currency conversion.
    loadExchangeRates().then(() => {
        if (allResults.length > 0) {
            displayResults(allResults);
        }
    });
}


if (document.readyState === "loading") {
    document.addEventListener(
        "DOMContentLoaded",
        initializeTuitionComparison
    );
} else {
    initializeTuitionComparison();
}
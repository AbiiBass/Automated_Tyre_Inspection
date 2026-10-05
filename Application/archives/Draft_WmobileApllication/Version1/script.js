/* ============================================
   SMRT Tyre Inspection Demo
   script.js (Part 1)
============================================ */

// ---------- Global Variables ----------

let technicianID = "";
let inspections = JSON.parse(localStorage.getItem("inspections")) || [];

let currentVehicle = "";
let editingIndex = -1;

// ---------- Login ----------

function login(){

    const id = document.getElementById("techID").value.trim();

    if(id === ""){
        alert("Please enter Technician ID.");
        return;
    }

    technicianID = id;

    document.getElementById("loginPage").classList.add("hidden");
    document.getElementById("dashboardPage").classList.remove("hidden");

    document.getElementById("welcomeText").innerHTML =
        "Technician ID : <b>" + technicianID + "</b>";

    loadCards();

}

// ---------- Navigation ----------

function goHome(){

    hideAllPages();

    document.getElementById("dashboardPage").classList.remove("hidden");

    loadCards();

}

function hideAllPages(){

    document.getElementById("dashboardPage").classList.add("hidden");
    document.getElementById("inspectionPage").classList.add("hidden");
    document.getElementById("detailsPage").classList.add("hidden");

}

// ---------- Vehicle Popup ----------

function openVehicleSelector(){

    document.getElementById("vehiclePopup").classList.remove("hidden");

}

function closeVehicleSelector(){

    document.getElementById("vehiclePopup").classList.add("hidden");

}

// ---------- New Inspection ----------

function startInspection(type){

    currentVehicle = type;

    editingIndex = -1;

    closeVehicleSelector();

    hideAllPages();

    document.getElementById("inspectionPage").classList.remove("hidden");

    document.getElementById("vehicleTitle").innerHTML =
        type + " Inspection";

    document.getElementById("plateImage").value = "";
    document.getElementById("odoImage").value = "";

    generateTyreInputs(type);

}

// ---------- Generate Tyres ----------

function generateTyreInputs(type){

    let tyreCount = 4;

    if(type === "Single Bus")
        tyreCount = 6;

    if(type === "Double Decker")
        tyreCount = 8;

    let html = "";

    for(let i=1;i<=tyreCount;i++){

        html += `
        <div class="tyre-card">

            <h4>Tyre ${i}</h4>

            <input
                type="number"
                class="tyreDepth"
                placeholder="Tread Depth (mm)"
            >

        </div>
        `;

    }

    document.getElementById("tyreFields").innerHTML = html;

}

// ---------- Dashboard ----------

function loadCards(){

    const list = document.getElementById("inspectionList");

    list.innerHTML = "";

    if(inspections.length===0){

        list.innerHTML =
        `
        <div style="
            text-align:center;
            color:gray;
            margin-top:100px;
            font-size:18px;
        ">
            No inspections submitted yet.
        </div>
        `;

        return;

    }

    inspections.forEach((item,index)=>{

        let icon = "directions_car";
        let colour = "car";

        if(item.vehicle==="Single Bus"){

            icon = "airport_shuttle";
            colour = "single";

        }

        if(item.vehicle==="Double Decker"){

            icon = "directions_bus";
            colour = "double";

        }

        const card = document.createElement("div");

        card.className = "card";

        card.innerHTML = `

            <div class="card-left">

                <div class="card-icon ${colour}">

                    <span class="material-icons">

                        ${icon}

                    </span>

                </div>

                <div>

                    <h3>${item.plateName}</h3>

                    <p>${item.vehicle}</p>

                </div>

            </div>

            <div class="card-right">

                <small>

                    ${item.modified}

                </small>

                <br><br>

                <button onclick="event.stopPropagation(); editInspection(${index})">

                    ✏️

                </button>

                <button onclick="event.stopPropagation(); deleteInspection(${index})">

                    🗑️

                </button>

            </div>

        `;

        card.onclick = function(){

            showDetails(index);

        };

        list.appendChild(card);

    });

}

// ---------- Save ----------

function saveData(){

    localStorage.setItem(
        "inspections",
        JSON.stringify(inspections)
    );

}

// ---------- Delete ----------

function deleteInspection(index){

    if(confirm("Delete this inspection?")){

        inspections.splice(index,1);

        saveData();

        loadCards();

    }

}

// ---------- Edit ----------

function editInspection(index){

    const item = inspections[index];

    const created = new Date(item.created);

    const now = new Date();

    const hours =
        (now-created)/(1000*60*60);

    if(hours>24){

        alert("Editing period has expired.");

        return;

    }

    editingIndex = index;

    currentVehicle = item.vehicle;

    hideAllPages();

    document.getElementById("inspectionPage")
        .classList.remove("hidden");

    document.getElementById("vehicleTitle").innerHTML =
        "Edit Inspection";

    generateTyreInputs(item.vehicle);

    const inputs =
        document.querySelectorAll(".tyreDepth");

    for(let i=0;i<inputs.length;i++){

        inputs[i].value = item.tyres[i];

    }

}

/* ============================================
   script.js (Part 2)
   Continue directly after Part 1
============================================ */

/* ---------- Submit Button ---------- */

function submitInspection(){

    document.getElementById("confirmPopup")
        .classList.remove("hidden");

}

function closeConfirm(){

    document.getElementById("confirmPopup")
        .classList.add("hidden");

}

/* ---------- Confirm Submission ---------- */

function confirmSubmit(){

    closeConfirm();

    const plateInput =
        document.getElementById("plateImage");

    const odoInput =
        document.getElementById("odoImage");

    // Get image filenames only
    let plateName = "";
    let odoName = "";

    if(plateInput.files.length > 0)
        plateName = plateInput.files[0].name;

    if(odoInput.files.length > 0)
        odoName = odoInput.files[0].name;

    // Read tyre values
    const tyreInputs =
        document.querySelectorAll(".tyreDepth");

    let tyres = [];

    tyreInputs.forEach(input=>{

        tyres.push(input.value);

    });

    // Current Date & Time

    const now = new Date();

    const timestamp =
        now.toLocaleDateString() +
        " " +
        now.toLocaleTimeString();

    // ----------------------------
    // NEW INSPECTION
    // ----------------------------

    if(editingIndex==-1){

        inspections.push({

            technician: technicianID,

            vehicle: currentVehicle,

            plateName: plateName=="" ?
                "Unknown Plate" :
                plateName,

            odometerImage: odoName,

            tyres: tyres,

            created: now,

            modified: timestamp

        });

    }

    // ----------------------------
    // EDIT EXISTING
    // ----------------------------

    else{

        inspections[editingIndex].plateName =
            plateName=="" ?
            inspections[editingIndex].plateName :
            plateName;

        inspections[editingIndex].odometerImage =
            odoName=="" ?
            inspections[editingIndex].odometerImage :
            odoName;

        inspections[editingIndex].tyres =
            tyres;

        inspections[editingIndex].modified =
            timestamp;

    }

    saveData();

    alert("Inspection submitted successfully.");

    goHome();

}

/* ---------- Details Page ---------- */

function showDetails(index){

    hideAllPages();

    document.getElementById("detailsPage")
        .classList.remove("hidden");

    const item = inspections[index];

    let tyreHTML = "";

    item.tyres.forEach((value,i)=>{

        tyreHTML +=
        `
        <p>
            <b>Tyre ${i+1}</b> :
            ${value} mm
        </p>
        `;

    });

    document.getElementById("detailsContent").innerHTML =

    `
    <h2>${item.vehicle}</h2>

    <hr><br>

    <p><b>Technician</b></p>
    <p>${item.technician}</p>

    <br>

    <p><b>Vehicle Plate Image</b></p>
    <p>${item.plateName}</p>

    <br>

    <p><b>Odometer Image</b></p>
    <p>${item.odometerImage}</p>

    <br>

    <p><b>Last Modified</b></p>
    <p>${item.modified}</p>

    <br>

    <h3>Tyre Readings</h3>

    ${tyreHTML}

    <br>

    <button class="submitBtn"
        onclick="editInspection(${index})">

        Edit Inspection

    </button>

    `;

}

/* ============================================
   script.js (Part 3)
   Continue after Part 2
============================================ */

/* ---------- Export CSV ---------- */

function exportCSV(){

    if(inspections.length===0){

        alert("No inspection records found.");
        return;

    }

    let csv =
        "Technician ID,Vehicle Type,Plate Image,Odometer Image,Created,Last Modified,Tyre Readings\n";

    inspections.forEach(item=>{

        let tyreString = item.tyres.join(" | ");

        csv +=
            `"${item.technician}",` +
            `"${item.vehicle}",` +
            `"${item.plateName}",` +
            `"${item.odometerImage}",` +
            `"${new Date(item.created).toLocaleString()}",` +
            `"${item.modified}",` +
            `"${tyreString}"\n`;

    });

    const blob = new Blob([csv],{
        type:"text/csv"
    });

    const url = window.URL.createObjectURL(blob);

    const a = document.createElement("a");

    a.href = url;

    a.download = "SMRT_Inspection_Data.csv";

    a.click();

    window.URL.revokeObjectURL(url);

}

/* ---------- Clear Form ---------- */

function clearForm(){

    document.getElementById("plateImage").value = "";
    document.getElementById("odoImage").value = "";

    const tyres =
        document.querySelectorAll(".tyreDepth");

    tyres.forEach(input=>{

        input.value = "";

    });

}

/* ---------- Sample Data (Optional) ---------- */

function loadSampleData(){

    if(inspections.length>0)
        return;

    inspections = [

        {

            technician:"1001",

            vehicle:"Car",

            plateName:"SGA1234A.jpg",

            odometerImage:"odo001.jpg",

            tyres:["7.2","7.1","7.0","7.3"],

            created:new Date(),

            modified:new Date().toLocaleString()

        },

        {

            technician:"1002",

            vehicle:"Single Bus",

            plateName:"SBS5678Z.jpg",

            odometerImage:"odo002.jpg",

            tyres:["9.1","9.0","8.8","8.7","9.2","9.0"],

            created:new Date(),

            modified:new Date().toLocaleString()

        }

    ];

    saveData();

}

/* ---------- Storage Initialisation ---------- */

window.onload = function(){

    // Uncomment this if you want demo cards
    // loadSampleData();

};

/* ---------- Keyboard Shortcut ---------- */
/* Press ESC to close popups */

document.addEventListener("keydown",function(e){

    if(e.key==="Escape"){

        document.getElementById("vehiclePopup")
            .classList.add("hidden");

        document.getElementById("confirmPopup")
            .classList.add("hidden");

    }

});

/* ---------- Version ---------- */

console.log("=====================================");
console.log("SMRT Tyre Inspection Prototype");
console.log("Version 1.0");
console.log("HTML + CSS + JavaScript");
console.log("=====================================");
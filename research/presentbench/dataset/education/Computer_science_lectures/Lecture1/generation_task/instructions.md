You are an expert lecturer. Your task is to create a **complete set of lecture slides** intended for **in-class teaching at the undergraduate level**. The slides must faithfully present and explain the content of the provided university-level textbook chapters.


---

## 1. Structure Requirements

The slide deck MUST have **21-35 slides**.

The slide deck must contain the following **ordered sections**, with specific coverage requirements for each. The number of slides in each section may be determined as appropriate.

For any claims or conclusions presented in the slides, it is not sufficient to state the result alone; an appropriate explanation of the underlying principles or reasons must also be provided.

1. **Title Slide**
    * **Course/Book Title:** "Computer Science: An Overview (11th Edition)"
    * **Chapter Focus:** Chapter 1: Data Storage
    * **Context:** Introduction to the fundamental representation of information in computing systems.

2. **Agenda / Outline**
    * **Chapter Theme:** The Transition from Analog Information to Digital Representation.
    * **Core Topics:** Bits & Logic, Storage Hardware (Memory/Disks), Data Representation (Text/Media), Binary Arithmetic, and Data Integrity (Compression/Errors).
    * **Goal:** To understand how diverse information (text, audio, video, numbers) is homogenized into bit patterns and stored reliably.

3. **Theoretical Foundations: Bits and Boolean Logic (Section 1.1)**
    * **The Bit:** Define the bit (binary digit) as a symbol whose meaning depends on application (numeric vs. status).
    * **Boolean Operations:** Present the mathematical definitions of AND, OR, XOR, and NOT.
    * **Gate Abstraction:** Illustrate how boolean operations map to physical hardware gates.
    * **Flip-Flops:** Explain the abstraction of a flip-flop circuit as the fundamental unit of storage (setting 0 or 1).
    * **Hexadecimal Notation:** Define the mapping between 4-bit patterns and Hex symbols ($0-F$) to simplify stream representation.

4. **Hardware Reality: Main Memory vs. Mass Storage (Sections 1.2 & 1.3)**
    * **Main Memory (RAM):**
        * Define memory organization: Cells (typically 8 bits/1 byte) and Addresses.
        * Explain the ordering of bits: Most Significant Bit (High-order) vs. Least Significant Bit (Low-order).
        * Discuss RAM volatility and the distinction between DRAM and SDRAM.
        * **Measurement:** Clarify the "Power of 2" (1024) vs. "Power of 10" (1000) terminology for KB, MB, GB.
    * **Mass Storage:**
        * **Magnetic Systems:** Explain disks, tracks, sectors, cylinders, and zoned-bit recording. Define performance metrics: Seek time, Rotation delay (latency), Access time, Transfer rate.
        * **Optical Systems:** Contrast CD/DVD spiral tracks with magnetic concentric tracks. Explain the transition from pits/lands to laser reflection.
        * **Flash Drives:** Explain the shift to non-volatile silicon dioxide chambers (no moving parts) and the concept of wear (flash memory degradation).
        * **Buffering:** Explain the mismatch between logical records (user view) and physical records (disk view), and the role of buffers in bridging this gap.

5. **Data Representation: Text and Multimedia (Section 1.4)**
    * **Text Encoding:**
        * **ASCII:** 7-bit standard + 0 padding.
        * **Unicode:** 16-bit standard to support international languages (65,536 patterns).
    * **Images:**
        * **Bit Maps:** Pixel representation, RGB encoding (3 bytes/pixel), and Luminance/Chrominance encoding.
        * **Vector/Geometric:** Representing images as geometric structures (scalable) vs. fixed pixels (aliasing/grainy).
    * **Audio:**
        * **Sampling:** Explain the digitization of a continuous wave via sampling rates (e.g., 44,100 samples/sec).
        * **MIDI:** Contrast sampled sound with MIDI (encoding the *instruction* to play a note, not the sound itself).

6. **The Binary System (Section 1.5)**
    * **Binary Notation:** Explain the positional quantity system (powers of 2: ...8, 4, 2, 1).
    * **Algorithm:** Present the division-by-two algorithm for converting positive integers to binary strings.
    * **Binary Addition:** Demonstrate binary addition rules ($1+1=10$, carrying the 1).
    * **Fractions:** Explain the radix point for fractional values (powers of $2^{-1}, 2^{-2}$, etc.).

7. **Numeric Storage: Integers (Section 1.6)**
    * **Two's Complement Notation:**
        * Define the sign bit (0=positive, 1=negative).
        * **The Algorithm:** Explain the "copy bits right-to-left until first 1, then complement the rest" method for negation.
        * **Addition/Subtraction:** Show that subtraction is simply adding a negated value.
    * **Overflow:** Define overflow in fixed-width storage (e.g., adding two positives yields a negative).
    * **Excess Notation:** Define Excess systems (e.g., Excess 8) where the zero value is shifted.

8. **Numeric Storage: Fractions & Floating Point (Section 1.7)**
    * **Floating-Point Components:** Define the three fields: Sign bit, Exponent field (often Excess notation), and Mantissa field.
    * **Normalization:** Explain the rule of starting the mantissa with the leftmost 1.
    * **Truncation Errors:**
        * Illustrate the inability to represent certain values (e.g., $2 \frac{5}{8}$ in a specific small-bit system) due to mantissa size limits.
        * Discuss the order of addition problem (adding small numbers to large numbers) leading to precision loss.

9. **Data Compression (Section 1.8)**
    * **Lossless vs. Lossy:** Define the distinction.
    * **Run-Length Encoding:** Explanation of compressing repetitive sequences.
    * **Frequency-Dependent (Huffman):** Using short codes for frequent items (e.g., 'e', 't') and long codes for rare items ('z', 'x').
    * **Dictionary Encoding (LZW):** Explain the adaptive dictionary concept using the "xyx xyx" example.
    * **Media Specifics:**
        * **Images:** GIF (Palette limitations), JPEG (Baseline standard, exploiting eye sensitivity to brightness vs. color).
        * **Audio/Video:** MPEG/MP3 (Temporal and Frequency masking).

10. **Data Integrity: Communication Errors (Section 1.9)**
    * **Parity Bits:**
        * **Odd vs. Even Parity:** How adding one bit allows detection of single-bit errors.
        * **Limitations:** Inability to detect even numbers of errors.
    * **Error-Correcting Codes:**
        * **Hamming Distance:** Define as the number of differing bits between two patterns.
        * **Correction Logic:** Explain how a sufficient Hamming distance allows a receiver to determine the intended pattern despite errors.

---

## 2. Content Constraints

* **Converage:** You must cover all major concepts, specifically:
   * **Definitions:** Every key concept introduced in the text must appear on at least one slide.
   * **Formal Models:** Mathematical definitions, rules, invariants, or algebraic properties must be explicitly explained.
   * **Examples:** Important worked illustrations from the text must be included and explained step-by-step.
   * **Summaries:** Each major chapter or subsection must end with a concise summary slide.
   * For each key diagram, table, or visual example included in the textbook, you must provide a corresponding slide that describes or reconstructs the figure.
   * *Note: You may **not omit** material simply because it is technical or detailed.*
* **Faithfulness to background materials**: Use only the information in the material. Do not introduce new terminology, external examples, or additional theorems not in the text. You must not fabricate factual content or modify or reinterpret the authors' claims.
* **Accuracy:** All content must be factually accurate, especially quantitative content and facts.
* **Brevity:** Use short, concise phrases, not long paragraphs. Focus on summarizing key facts and events without excessive detail. Bullet points may be used for clarity. If you use bullet points, each slide should have no more than 6 bullet points.
* **Sufficient Depth**: Do not summarize the material in an overly superficial or high-level manner. The slides should preserve essential technical details, key arguments, and substantive insights rather than only presenting vague conclusions.
* **Logical Flow:** The slides should present a clear narrative, starting from early space exploration to recent developments. Ensure there is a clear progression of time and events.
* **Relevance of Information**: You must not add unrelated content.
* **Code & Markup Formatting**: Avoid raw LaTeX or Markdown code unless necessary.
* **Citation & Referencing**: 
    * **Every slide** must explicitly indicate which textbook chapter(s) and section(s) its content comes from.
    * The attribution must refer to the **most fine-grained section level possible** (e.g., “Section 2.3.2–2.3.5”).
    * Slides without explicit chapter/section attribution are considered **incorrect**.
    * Accurately reference the textbook's diagrams, and examples. If a slide uses data from the textbook, you must clearly indicate the source of the data on that slide (e.g., page xx, Figure xx, Table xx).
    * All references (if any) must be placed in the bottom-left corner of the slide.


## 3. Visual & Design

* **Charts and Diagrams:** Use appropriate charts and diagrams where needed to visually present and clarify information, rather than relying only on text (and demos).
  * If the slide includes charts or figures, ensure that all visual elements are clearly annotated (e.g., axes are labeled, units are specified, legends are included where needed, and data points are explained when necessary).
  * Include **figures or diagrams descriptions** when appropriate, e.g., “The chart (from page 4 in the paper) shows proprietary models outperform open-weight ones.”
* **Images:** Include relevant images if necessary. Images must be high quality, clearly labeled, and relevant to the content.
* **Legibility:** Use legible fonts and avoid clutter. Text should be large enough to be easily read.
* **Visual Balance:** Balance text and visuals so slides are easy to read when projected.
* **Layout:** Maintain a clean, professional layout with appropriate fonts, colors, and formatting.
* **Style Consistency**: The entire slide deck should follow a unified and coherent visual style.
* **Information Load**: Slides should avoid excessive information per page to preserve readability.


## 4. Text Quality

* All generated text should be clear, with no missing or incorrect characters or words.
* Spelling, grammar, and typography must be accurate and correct throughout the content.

## 5. Technical Fidelity Requirements

The slide deck must include at least 7 slides containing quantitative content, such as mathematical formulas, calculation examples, worked examples, or experimental results. The entire slide deck must not rely solely on high-level natural language explanations.

* Experimental data and constants must exactly match those presented in the textbook.
* Formulas must be identical to those in the textbook, mathematically equivalent, or derivable from textbook formulas.
* All calculations and reasoning must follow the rules and methods described in the textbook and be logically correct.
* Ensure that any figures and tables in your slide deck are consistent with the textbook. Specifically, for every figure and table in the slides:
    * If it is directly copied from the textbook, clearly indicate on the slide which figure or table it corresponds to in the textbook (e.g., Figure 1 in the textbook, Table 2 in the textbook).
    * If it is newly plotted based on data from the textbook, clearly specify which section of the textbook the data are taken from (e.g., Section 3.1). In addition, provide a clear explanation of the meaning of each legend item in the figure and each row and column in the table.
* Mathematical figures (e.g., function plots) must be logically equivalent to the corresponding textbook content, not merely similar in shape.
* If statistical charts (such scatter plots, line charts or radar charts) are used in the slide deck, ensure that every data point exactly matches the corresponding data point in the original figure from the textbook. Note that the values must be **precisely** the same, not merely that the overall trends align.
* The slides may include data used for conceptual illustration or experimental data. However, You must clearly indicate on the corresponding slide which data are conceptual illustrations or experimental data reported in the textbook.


## 5. Presentation Tone and Audience

* **Tone**:
    * Academic, clear, and instructional. 
    * Maintain consistent tone and formatting.
    * No conversational language, rhetorical questions, emojis, jokes, or storytelling.
    * Use precise technical language appropriate for a computer science course.
* **Audience:** Undergraduate students encountering this material for the first time.
* **Goal:** Help students understand core concepts, follow logical derivations, interpret figures/tables, and connect mechanisms to reasoning.
* **Prerequisite:** Avoid assuming prior knowledge beyond standard prerequisite courses.

Your generated slide deck should be able to be used directly in a classroom.

---

# **Output Expected**

A **complete slide deck** satisfying all constraints above.


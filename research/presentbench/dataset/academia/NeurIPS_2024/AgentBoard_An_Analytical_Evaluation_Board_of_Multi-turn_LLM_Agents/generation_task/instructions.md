You are to generate a complete, conference-quality academic slide deck suitable for an oral presentation at a top-tier AI conference (e.g., NeurIPS / ICML / ICLR / AAAI), based strictly on the paper. The slides must be accurate, well-structured, and **faithful to the original paper**, with no fabricated content.

---

# **Strict Constraints for the Slides**

Below are the **hard constraints** you MUST satisfy. Slides violating these constraints are considered **incorrect**.

## 1. Content Requirements

The slide deck must have **16-20 slides**.

The slide deck must include the following sections, in the order listed below (the number of slides in each section may be determined as appropriate).

1. **Title Slide**

   * Paper title
   * Authors
   * Conference (if applicable)
   * Author's names

2. **Outline / Agenda**

3. **Introduction / Background**

   * Context of LLM agents
   * Why multi-turn, partially observable environments matter

4. **Motivation & Problem Statement**

   * What existing benchmarks lack
   * Why evaluating agentic abilities is challenging

5. **Overview of AgentBoard**

   * Unified task design
   * Multi-turn + partially observable features
   * Task diversity (refer to details such as 9 tasks, 1012 environments, 6-20 turns)

6. **Task Categories & Examples**

   * Web tasks
   * Embodied AI tasks
   * Tool tasks
   * Game tasks
   * Explain what each category evaluates

7. **Unified Framework**

   * Inputs: Instruction, Goal, Observation, Memory
   * Outputs: Actions
   * Highlight multi-turn memory usage

8. **Fine-grained Evaluation Metrics**

   * Why success rate is insufficient
   * Progress rate:
     * Match-based

9. **Experimental Results**

   * Proprietary vs open-weight models
   * Progress rate vs success rate
   * Effects of coding ability
   * Effects of agent tuning

10. **Analysis of Agent Abilities**

    * Grounding ability
    * Reflection ability
    * Long-range planning

11. **Key Takeaways**

    * What makes strong agent models
    * Why fine-grained evaluation matters

12. **Limitations & Future Work**

    * Discuss limitations mentioned or implied in the paper

13. **Conclusion**



## 2. Content Constraints

* **Faithfulness to background materials**: Use only the information in the paper. You must not fabricate additional experiments or modify or reinterpret the authors' claims.
* **Accuracy:** All content must be factually accurate, especially quantitative content and facts.
* **Brevity:** Use short, concise phrases, not long paragraphs. Focus on summarizing key facts and events without excessive detail. Bullet points may be used for clarity. If you use bullet points, each slide should have no more than 6 bullet points.
* **Sufficient Depth**: Do not summarize the paper in an overly superficial or high-level manner. The slides should preserve essential technical details, key arguments, and substantive insights rather than only presenting vague conclusions.
* **Logical Flow:** The slides should present a clear narrative, starting from early space exploration to recent developments. Ensure there is a clear progression of time and events.
* **Relevance of Information**: You must not add unrelated content.
* **Code & Markup Formatting**: Avoid raw LaTeX or Markdown code unless necessary.
* **Citation & Referencing**: Accurately reference the paper's results, diagrams, and examples. 
   * If a slide uses data from the paper, you must clearly indicate the source of the data on that slide (e.g., page xx, Figure xx, Table xx).
   * All references (if any) must be placed in the bottom-left corner of the slide.

## 3. Visual & Design

* **Images:** Include relevant images. Images must be high quality, clearly labeled, and relevant to the content.
* **Charts and Diagrams:** Use appropriate charts and diagrams where needed to visually present and clarify information, rather than relying only on text (and demos).
  * If the slide includes charts or figures, ensure that all visual elements are clearly annotated (e.g., axes are labeled, units are specified, legends are included where needed, and data points are explained when necessary).
  * Include **figures or diagrams descriptions** when appropriate, e.g., “The chart (from page 4 in the paper) shows proprietary models outperform open-weight ones.”
* **Legibility:** Use legible fonts and avoid clutter. Text should be large enough to be easily read.
* **Visual Balance:** Balance text and visuals so slides are easy to read when projected.
* **Layout:** Maintain a clean, professional layout with appropriate fonts, colors, and formatting.
* **Style Consistency**: The entire slide deck should follow a unified and coherent visual style.
* **Information Load**: Slides should avoid excessive information per page to preserve readability.

## 4. Text Quality

* All generated text should be clear, with no missing or incorrect characters or words.
* Spelling, grammar, and typography must be accurate and correct throughout the content.

## 5. Technical Fidelity Requirements

* **Quantitative Coverage**: Ensure that key data and experimental results (possibly presented in charts or tables in the paper) are included in the slide deck. In other words, the presentation should not only discuss the ideas of the paper but also present specific quantitative details (e.g., statistical data, experimental results, etc.).
   * The slide deck must include at least 5 slides with quantitative details.

* **Quantitative Detail Correctness**: Ensure quantitative details (task counts, benchmark size, etc.) are correct.

* **Table & Chart Traceability and Annotation**: Ensure that any figures and tables in your slide deck are consistent with the paper. Specifically, for every figure and table in the slides:
  * If it is directly copied from the paper, clearly indicate on the slide which figure or table it corresponds to in the paper (e.g., Figure 1 in the paper, Table 2 in the paper).
  * If it is newly plotted based on data from the paper, clearly specify which section of the paper the data are taken from (e.g., Section 3.1). In addition, provide a clear explanation of the meaning of each legend item in the figure and each row and column in the table.
  * For charts, every axis, unit, and label must be explicit

* **Point-Level Accuracy for Plots**: If scatter plots, line charts or radar charts are used in the slide deck, ensure that every data point exactly matches the corresponding data point in the original figure from the paper. Note that the values must be **precisely** the same, not just the shape of the graph.

* **Conceptual Illustration**: The slides may include data used only for conceptual illustration. However, if such data are included, you must clearly indicate on the corresponding slide which data are conceptual illustrations rather than experimental data reported in the paper.

## 5. Presentation Tone and Audience

* **Tone:** The tone should be informative, academic, and professional. It should avoid casual or informal conversational language, while remaining clear and suitable for oral presentation. The slide deck should maintain a consistent tone.
* **Audience:** The presentation is intended for an academic audience with relevant background knowledge in the field. The content should be accessible to graduate-level students and researchers, assuming familiarity with standard concepts and terminology, while still providing sufficient context to understand the motivation, methodology, and key contributions.

---

# **Output Expected**

A **complete slide deck** satisfying all constraints above.

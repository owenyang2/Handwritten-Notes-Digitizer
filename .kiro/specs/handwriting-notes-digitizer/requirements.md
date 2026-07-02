# Requirements Document

## Introduction

The Handwriting Notes Digitizer is a web application that converts images of handwritten English notes into editable digital text, which users can copy or export into note-taking applications such as Notion, Obsidian, and Google Docs. Unlike commodity OCR engines that perform well on printed text but poorly on handwriting, this product personalizes recognition to an individual writer over time.

The hero feature is a human-in-the-loop correction loop: when a user corrects a wrong prediction inline, each correction is captured as a labeled training pair (handwriting image region paired with the corrected text). These pairs feed a personalization pipeline that adapts a general handwriting recognition model to the individual writer using parameter-efficient fine-tuning, producing a small per-user adapter rather than a full per-user model. An optional calibration flow lets users provide known-label samples up front to address the cold-start problem; calibration feeds the same personalization pipeline as the correction loop.

The product scope is deliberately constrained to English handwriting done well. Personalization must improve accuracy on the user's own handwriting (measured by Character Error Rate and Word Error Rate) while guarding against overfitting on small data and catastrophic forgetting of general accuracy. The end goal is a reproducible, Dockerized, deployable demo that shows a visible before/after accuracy improvement.

## Glossary

- **System**: The complete Handwriting Notes Digitizer application, including frontend, backend services, and recognition models.
- **HTR_Engine**: The handwriting text recognition component that maps a handwriting image to recognized text at the word and line level.
- **Recognition_Service**: The backend service that accepts an uploaded image, invokes the HTR_Engine, and returns recognized text with associated image regions.
- **Editor**: The frontend component that displays recognized text and supports inline editing by the user.
- **Correction_Pipeline**: The component that captures user corrections as labeled training pairs and stores them for personalization.
- **Training_Pair**: A labeled data record consisting of a handwriting image region and its corresponding corrected text.
- **Personalization_Service**: The component that adapts the base recognition model to an individual user using accumulated Training_Pairs via parameter-efficient fine-tuning.
- **Adapter**: A small per-user set of fine-tuned parameters (for example, LoRA-style adapter weights or last-layer weights) applied on top of the shared base model for a specific user.
- **Base_Model**: The shared, general handwriting recognition model trained on a public English dataset, used as the starting point for all users.
- **Evaluation_Service**: The component that computes Character Error Rate and Word Error Rate and reports accuracy before and after personalization.
- **Export_Service**: The component that produces copyable plain-text or markdown output of the edited text.
- **Calibration_Service**: The optional component that presents prompt sentences for the user to handwrite, capturing known-label samples up front.
- **CER**: Character Error Rate, the Levenshtein edit distance between predicted and reference text at the character level divided by the number of reference characters.
- **WER**: Word Error Rate, the Levenshtein edit distance between predicted and reference text at the word level divided by the number of reference words.
- **User**: An authenticated individual whose handwriting is recognized and personalized.
- **Held_Out_Set**: A reserved subset of a User's labeled samples that is excluded from training and used only for evaluation.

## Requirements

### Requirement 1: Image Upload and Submission

**User Story:** As a user, I want to upload an image of my handwritten notes, so that I can have it converted into digital text.

#### Acceptance Criteria

1. WHEN an authenticated user submits a file whose decoded content is a valid PNG or JPEG image larger than 0 bytes and no larger than 10 megabytes, THE Recognition_Service SHALL accept the image and initiate recognition.
2. IF a user submits a file whose decoded content is not a PNG or JPEG image, THEN THE Recognition_Service SHALL reject the file without initiating recognition and return a message identifying PNG and JPEG as the supported formats.
3. IF a user submits an image file larger than 10 megabytes, THEN THE Recognition_Service SHALL reject the file without initiating recognition and return a message stating that the maximum allowed size is 10 megabytes.
4. IF a user submits a file that is empty or whose content cannot be decoded as an image, THEN THE Recognition_Service SHALL reject the file without initiating recognition and return a message indicating that the file is not a valid image.
5. IF an unauthenticated user submits a file, THEN THE Recognition_Service SHALL reject the submission without initiating recognition and SHALL NOT store the file.
6. WHEN an image is accepted, THE Recognition_Service SHALL associate the image with the submitting user's account.

### Requirement 2: Handwritten Text Recognition

**User Story:** As a user, I want the system to recognize the handwritten text in my image at the word and line level, so that I receive a usable digital transcription.

#### Acceptance Criteria

1. WHEN an accepted image contains handwritten English text, THE HTR_Engine SHALL produce recognized text for every line and word region it detects in the image.
2. THE HTR_Engine SHALL produce recognized text segmented into lines and words, where each detected line contains one or more recognized words rather than only isolated single characters.
3. WHEN recognition completes for an accepted image containing up to 100 words, THE Recognition_Service SHALL return the recognized text within 10 seconds of image acceptance.
4. WHEN recognition produces text, THE Recognition_Service SHALL associate each recognized word with the coordinates of the image region from which the word was recognized.
5. IF the HTR_Engine cannot detect any handwritten text in an accepted image, THEN THE Recognition_Service SHALL return a result indicating that no text was recognized.
6. IF recognition for an accepted image does not complete within 10 seconds, THEN THE Recognition_Service SHALL stop the recognition attempt and return an error result indicating that recognition timed out, while retaining the accepted image associated with the user's account.
7. IF the HTR_Engine fails to process an accepted image, THEN THE Recognition_Service SHALL return an error result indicating that recognition failed without modifying the user's stored data.

### Requirement 3: Display of Recognized Output

**User Story:** As a user, I want to see the recognized text alongside my original image, so that I can verify the transcription.

#### Acceptance Criteria

1. WHEN the Recognition_Service returns recognized text, THE Editor SHALL display the recognized text to the user within 2 seconds of receiving the result.
2. WHEN the Recognition_Service returns recognized text, THE Editor SHALL display the recognized text in reading order with lines ordered top to bottom and words within each line ordered left to right, following the line and word order detected in the image.
3. WHEN the Recognition_Service returns recognized text, THE Editor SHALL display the original uploaded image and the recognized text so that both are visible at the same time.
4. IF the Recognition_Service returns a result indicating that no text was recognized, THEN THE Editor SHALL display a message indicating that no text was recognized and SHALL display the original uploaded image.

### Requirement 4: Inline Correction of Recognized Text

**User Story:** As a user, I want to correct wrong predictions inline, so that I get an accurate transcription and improve future recognition of my handwriting.

#### Acceptance Criteria

1. THE Editor SHALL allow the user to edit any portion of the recognized text, accepting corrected text of up to 10,000 characters per recognized word.
2. WHEN a user edits a portion of the recognized text and confirms the edit, THE Editor SHALL update the displayed text to reflect the correction within 1 second of confirmation.
3. WHEN a user confirms a correction in which the corrected text differs from the recognized text for a word, THE Correction_Pipeline SHALL create a Training_Pair consisting of the corresponding image region and the corrected text.
4. IF a user confirms an edit whose text is identical to the recognized text for the affected word, THEN THE Correction_Pipeline SHALL leave the user's stored Training_Pairs unchanged.
5. WHEN a Training_Pair is created, THE Correction_Pipeline SHALL store the Training_Pair associated with the user's account within 5 seconds of creation.
6. IF storing a created Training_Pair fails, THEN THE Correction_Pipeline SHALL return an error indication to the user, retain the displayed corrected text in the Editor, and leave the user's previously stored Training_Pairs unchanged.
7. WHERE a user confirms a correction for a word that was already corrected, THE Correction_Pipeline SHALL retain the most recent corrected text for that image region.
8. WHILE a user edits text without confirming a correction, THE Correction_Pipeline SHALL leave the user's stored Training_Pairs unchanged.

### Requirement 5: Personalization via Parameter-Efficient Fine-Tuning

**User Story:** As a user, I want the system to learn my handwriting from my corrections, so that recognition of my handwriting improves over time.

#### Acceptance Criteria

1. WHEN a user has accumulated at least 20 Training_Pairs that have not yet been included in a completed Adapter training, THE Personalization_Service SHALL begin training an Adapter for that user using the user's Training_Pairs within 60 seconds of that count being reached.
2. THE Personalization_Service SHALL train each Adapter using parameter-efficient fine-tuning that updates only the Adapter parameters while keeping the Base_Model parameters unchanged.
3. THE Personalization_Service SHALL store exactly one Adapter per user rather than a full copy of the Base_Model per user.
4. WHEN an Adapter exists for a user, THE Recognition_Service SHALL apply that user's Adapter together with the Base_Model when recognizing that user's images.
5. IF no Adapter exists for a user, THEN THE Recognition_Service SHALL recognize that user's images using the Base_Model.
6. WHILE an Adapter training is in progress for a user, THE Personalization_Service SHALL not start a second concurrent Adapter training for that same user.
7. IF an Adapter training does not complete successfully, THEN THE Personalization_Service SHALL retain the user's previously active recognition configuration and record an indication that the training attempt did not succeed.

### Requirement 6: Mitigation of Overfitting and Catastrophic Forgetting

**User Story:** As a user, I want personalization to improve my handwriting recognition without degrading overall accuracy, so that the system remains reliable.

#### Acceptance Criteria

1. WHEN the Personalization_Service trains an Adapter, THE Personalization_Service SHALL reserve a Held_Out_Set consisting of 20 percent of the user's Training_Pairs, and no fewer than 5 Training_Pairs, that is excluded from training.
2. WHEN Adapter training completes, THE Evaluation_Service SHALL measure the CER and WER of the new Adapter on a general English evaluation set.
3. IF a newly trained Adapter increases CER on the general English evaluation set by more than 1.0 percentage point relative to the Base_Model, THEN THE Personalization_Service SHALL reject the new Adapter and retain the previously active recognition configuration for that user.
4. IF a newly trained Adapter increases WER on the general English evaluation set by more than 2.0 percentage points relative to the Base_Model, THEN THE Personalization_Service SHALL reject the new Adapter and retain the previously active recognition configuration for that user.
5. THE Personalization_Service SHALL stop Adapter training when the Held_Out_Set CER does not improve by at least 0.5 percentage point over 3 consecutive evaluation cycles, and in all cases no later than 50 training epochs.
6. WHEN the Personalization_Service rejects a newly trained Adapter, THE Personalization_Service SHALL record an indication that personalization did not improve recognition and notify the user.

### Requirement 7: Accuracy Measurement Before and After Personalization

**User Story:** As a user, I want to see how much personalization improved recognition of my handwriting, so that I can trust the system is learning.

#### Acceptance Criteria

1. WHILE a user's Held_Out_Set contains at least one reference character and at least one reference word, WHEN an Adapter is trained for that user, THE Evaluation_Service SHALL compute the CER and WER of the Base_Model on that user's Held_Out_Set.
2. WHILE a user's Held_Out_Set contains at least one reference character and at least one reference word, WHEN an Adapter is trained for that user, THE Evaluation_Service SHALL compute the CER and WER of the user's Adapter on that user's Held_Out_Set.
3. WHEN both the before-personalization and after-personalization measurements are available, THE Evaluation_Service SHALL report to the user the before-personalization CER and WER, the after-personalization CER and WER, and the change in CER and WER computed as the before value minus the after value.
4. THE Evaluation_Service SHALL compute CER as the character-level Levenshtein edit distance, counting substitutions, insertions, and deletions, between predicted and reference text divided by the number of reference characters, yielding a non-negative ratio.
5. THE Evaluation_Service SHALL compute WER as the word-level Levenshtein edit distance, counting substitutions, insertions, and deletions, between predicted and reference text divided by the number of reference words, yielding a non-negative ratio.
6. IF a user's Held_Out_Set contains no reference characters or no reference words, THEN THE Evaluation_Service SHALL report that the accuracy measurement is unavailable.
7. IF computing a CER or WER measurement fails, THEN THE Evaluation_Service SHALL report that the measurement is unavailable while retaining any successfully computed measurement.

### Requirement 8: Export and Copy of Digital Text

**User Story:** As a user, I want to copy or export the cleaned text, so that I can paste it into my note-taking application.

#### Acceptance Criteria

1. WHEN a user requests to copy the edited text, THE Export_Service SHALL provide the complete current edited text as plain text, preserving line breaks, within 2 seconds of the request.
2. WHEN a user requests to export the edited text as markdown, THE Export_Service SHALL provide the complete current edited text in markdown format, preserving line and paragraph structure, within 2 seconds of the request.
3. WHEN a user requests to copy or export text, THE Export_Service SHALL produce output that reflects every confirmed correction present in the Editor at the time of the request.
4. WHEN a user requests to copy or export text, THE Export_Service SHALL provide the export using the current edited text without waiting for Adapter training to complete.
5. IF a user requests to copy or export when the edited text is empty, THEN THE Export_Service SHALL return a message indicating that there is no text to export and SHALL produce no output.
6. IF a copy or export request fails, THEN THE Export_Service SHALL return a failure indication and leave the edited text unchanged.

### Requirement 9: Optional Calibration Flow

**User Story:** As a user, I want to optionally write a few provided sentences, so that the system can recognize my handwriting accurately from the start.

#### Acceptance Criteria

1. THE System SHALL present the calibration flow as an optional step, and THE System SHALL allow a user to recognize, personalize, and export text without completing or starting the calibration flow.
2. WHEN a user starts the calibration flow, THE Calibration_Service SHALL present between 3 and 10 prompt sentences, each between 1 and 200 characters in length, for the user to handwrite.
3. WHEN a user submits a handwriting image for a prompt sentence, THE Calibration_Service SHALL create a Training_Pair using the submitted image region and the known prompt sentence text.
4. IF a submitted handwriting image is empty, exceeds 10 megabytes in size, or contains no detectable handwriting region, THEN THE Calibration_Service SHALL reject the submission, display a message indicating the submission is invalid and the reason, and SHALL NOT create a Training_Pair.
5. WHEN a calibration Training_Pair is created, THE Calibration_Service SHALL store the Training_Pair using the same storage as Training_Pairs created by the Correction_Pipeline.
6. WHEN a user submits at least one valid handwriting image during the calibration flow, THE Personalization_Service SHALL train Adapters using calibration Training_Pairs and correction Training_Pairs through the same personalization pipeline.

### Requirement 10: User Account and Data Association

**User Story:** As a user, I want my handwriting data and personalization kept to my own account, so that my Adapter reflects only my handwriting.

#### Acceptance Criteria

1. THE System SHALL require a user to be authenticated before submitting images, corrections, or calibration samples.
2. THE Personalization_Service SHALL train each user's Adapter using only Training_Pairs associated with that user's account, and SHALL NOT use Training_Pairs associated with any other account.
3. WHEN recognizing a user's image, THE Recognition_Service SHALL apply only the requesting user's Adapter and SHALL NOT apply any other user's Adapter.
4. IF an unauthenticated request attempts to submit an image, correction, or calibration sample, THEN THE System SHALL reject the request, return a message indicating that authentication is required, and SHALL NOT store any submitted data.
5. IF a user attempts to access another user's Training_Pairs, recognized text, or Adapter, THEN THE System SHALL deny the access and SHALL NOT disclose the other user's data.

### Requirement 11: Reproducibility and Deployment

**User Story:** As a developer, I want the system to be reproducible and deployable as a demo, so that the before/after accuracy improvement can be demonstrated end to end.

#### Acceptance Criteria

1. THE System SHALL provide a containerized deployment definition that builds runnable frontend, backend, and recognition components from pinned dependency versions without requiring manual code modification during the build.
2. THE System SHALL train the Base_Model from a documented public English handwriting dataset using a documented training procedure that specifies the dataset version, the data split, and the training configuration.
3. WHEN the documented training procedure is re-executed on the same dataset and training configuration, THE System SHALL produce a Base_Model whose CER and WER on a fixed evaluation set differ from the previously produced Base_Model by no more than a configured tolerance.
4. WHEN the deployed System is run with all components available, THE System SHALL expose the image upload, recognition, inline correction, personalization, and export capabilities through the web user interface.
5. WHILE the System runs in a partial deployment, THE System SHALL continue to serve the capabilities whose required components are available.
6. IF a user requests a capability whose required components are unavailable during a partial deployment, THEN THE System SHALL return a response indicating that the requested capability is unavailable.

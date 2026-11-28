---
description: "Add movies on the Import page, pair two camera files, set the channel layout and the frame steps, and repair a project whose movie moved."
---

# Import movies and set their channels

## Who this page is for

You have movies from a dual-view or a two-camera setup, and you use the desktop
application. You want to tell Tether where the donor and the acceptor channel are,
before you extract traces. You can also repair a project whose movie file moved.
The page assumes that you can open the application and choose files in a file dialog.

## Before you start

- The movie files on a disk that this computer can read.
- For a repair, a project that is open writable in the application.

## 1. Add the movies

1. Open the **Prepare** section. The **Import** panel is on the left of the window.
2. Select **Add movies…** and choose one or more movie files.
   Alternatively, drop the files on the **Import** panel.
3. Select a movie in the list.

The details table shows each value of the movie, and where the value came from:

- *from the file*: Tether read the value from the movie file.
- *from you*: you gave the value.
- *unknown*: the file does not record the value.

The table shows the reader, the shape and the pixel type of the file. It also shows the
frame time, the pixel size and the channel names. A file that no reader opens stays out of
the list, and the message under the panel gives the reason.

## 2. Pair two camera files

Do this step only when the donor and the acceptor are in two files.

1. Select the two movies in the list. The upper movie becomes the donor file.
2. Select **Pair two movies**.
3. To exchange the two files, select **Swap donor and acceptor**.

Tether refuses a pair whose two files differ in frame count, frame time, pixel type or frame
size. The message names each value that differs. Tether does not align two movies in time.
Select **Unpair** to split a pair into two movies again.

## 3. Set the channel layout and the frame steps

1. In **Layout**, choose `side-by-side`, `top-bottom` or `rectangles`.
2. In **Donor**, choose the half that holds the donor channel.
3. For `rectangles`, type each rectangle as `y1, x1, y2, x2`.
4. In **Frame steps**, type the steps that turn the whole frame, for example `rotate 90`.
5. Look at the frame view. The donor and the acceptor trace colors outline the two channels.
6. Select **Apply**.

For a movie pair, each file is one channel, so the page hides **Layout**, **Donor** and the
rectangles. Skip steps 1 to 3. The frame steps and **Apply** stay.

Each change shows on the frame view at once, and the note says *Preview, not applied yet*.
Tether keeps the layout for the movie only after **Apply**. The frame steps run before the
channel split, so the layout reads its positions in the turned frame. A layout that does not
fit the frame is not applied, and the note gives the reason. Two rectangles that overlap are
allowed, with a warning.

Separate the frame steps with commas. Tether knows four steps:

- `rotate 90`, `rotate 180` or `rotate 270` turns the frame clockwise.
- `flip v`, `flip h` or `flip vh` reverses the rows, the columns, or both.
- `translate 2 -1` moves the frame 2 rows down and 1 column left.
- `crop 1 1 256 256` keeps rows 1 to 256 and columns 1 to 256.

No step resamples a pixel. A move leaves some pixels with no source, so Tether refuses a
`translate` that no later `crop` cuts away. For a 512 x 512 frame, type
`translate 2 -1, crop 3 1 512 511`. A rotate or a flip needs no crop.

## 4. Use the movies

- Select **Save recipe…** to write a recipe with one extract step for each applied movie.
  Each project is the movie name with `.tether`, in the folder of the movie. Run the
  recipe as [Run a recipe again with no window](run-a-recipe.md) tells you.
- Select **Use in extraction** to give the selected movie and its layout to the extraction
  page on the right.

A recipe step and the extraction page cannot hold a movie pair yet. The extraction page also
cannot take frame steps yet. For a movie with frame steps, save a recipe.

## 5. Repair an open project

The **Open project** group lists the source files of the project that is open.

To link a moved file again:

1. Select a source whose state is `file missing`.
2. Select **Link again…** and choose the file at its new place.

Tether reads the whole file and compares its checksum with the checksum that the project
holds. Tether links the file only when the two are equal. A large movie takes some time to
read, and the window stays live while Tether reads it.

To give a missing frame time:

1. Select a movie whose frame time is `unknown`.
2. Type the frame time in seconds and select **Give frame time**.
3. Check the value against the acquisition record of the movie.
4. Select **Confirm frame time**.

After step 2 the origin is *from you, not verified*. After step 4 it is *from you, confirmed*.
A rate in s⁻¹ is only as correct as the frame time behind it.
